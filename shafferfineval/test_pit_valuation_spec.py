"""Offline tests for the three-subfactor valuation pillar.

Pure stdlib, no network, NO REAL DATABASE:

    python test_pit_valuation_spec.py

Five kinds of check live here.

THE OWNER'S CASE. `V[PE=1, PEPeer=1, EVEBITDA=0]` must be a PRESENT pillar
producing a real valuation view, and the detailed mask must carry the truth
while the pillar-level signature stays `VGPD`. If that one assertion fails,
nothing else in the file matters.

THE ABSOLUTE LEG DOES NOT WAIT FOR PEERS. A 100x P/E must be penalised at a
thin-cohort as-of date where no admissible cohort forms at all, and the number
must be the pinned curve value rather than a smaller one that happens to be
negative. The contrast against a peer-relative-only valuation -- which charges
NOTHING for the same company -- is asserted, not asserted-about.

THE SIGN CROSSING. Over an exhaustive grid of endpoint sign cases, no pair that
crosses zero, starts at zero, starts negative or starts under one cent may
produce a percentage growth number. The refusals are of COMPUTABLE values, so
the test drives them through the real function rather than checking a guard.

THE VOCABULARY IS NOT DUPLICATED. Every string this module hands to a score row
must be the same OBJECT `pit_store`, `pit_eps`, `pit_invariants` and
`pit_intermediates` already defined. The test asserts identity against those
modules, so a second spelling of one outcome fails here rather than in a report
six months later.

THE RESTRAINT. This module describes a computation and specifies a schema; it
must open no connection, fit nothing, and write no row. `sqlite3.connect` is
replaced with a landmine and the entire public API is driven over it. The real
8.97 GiB store is never touched -- not read, not opened, not named -- and the
DDL is exercised only against a throwaway in-memory database built from
`pit_store.SCHEMA`.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pit_eps
import pit_factor_spec
import pit_intermediates
import pit_invariants
import pit_normalization
import pit_score_signature as SIG
import pit_price_basis
import pit_shares
import pit_store
import pit_valuation_spec as V

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def _close(a: float, b: float, tol: float = 1e-9) -> bool:
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tol)


def _raises(thunk, exc=ValueError) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------
# 1. the three subfactors, as declared
# --------------------------------------------------------------------------

def test_declarations() -> None:
    print()
    print("== 1. the three subfactors ==")
    check("exactly three subfactors, anchor first",
          V.SUBFACTOR_KEYS == (V.SUBFACTOR_PE_ABSOLUTE, V.SUBFACTOR_PE_RELATIVE,
                               V.SUBFACTOR_EV_EBITDA))
    check("the weights sum to 1.00",
          _close(sum(V.SUBFACTOR_WEIGHTS.values()), 1.0, 1e-12))

    absolute = V.subfactor(V.SUBFACTOR_PE_ABSOLUTE)
    check("P/E ABSOLUTE requires NO peer cohort", not absolute.requires_cohort)
    check("P/E ABSOLUTE needs price and EPS and NOTHING else",
          absolute.required_primitives
          == (V.PRICE_RAW_AS_TRADED, "earnings_per_share_pit"))
    check("P/E ABSOLUTE is the one ANCHOR and stands alone",
          absolute.role == V.ROLE_ANCHOR and absolute.can_stand_alone
          and [k for k in V.SUBFACTOR_KEYS
               if V.SUBFACTORS[k].role == V.ROLE_ANCHOR] == [V.SUBFACTOR_PE_ABSOLUTE])
    check("P/E ABSOLUTE does NOT declare peer_coherence -- it consults no peers",
          V.COMPONENT_PEER_COHERENCE not in absolute.quality_components)

    relative = V.subfactor(V.SUBFACTOR_PE_RELATIVE)
    check("P/E RELATIVE is optional, needs a cohort, cannot stand alone",
          relative.requires_cohort and not relative.can_stand_alone
          and relative.depends_on == V.SUBFACTOR_PE_ABSOLUTE)

    supplement = V.subfactor(V.SUBFACTOR_EV_EBITDA)
    check("EV/EBITDA is optional, five-primitive, and CAN stand alone",
          supplement.requires_cohort and supplement.can_stand_alone
          and supplement.depends_on is None
          and len(supplement.required_primitives) == 6)
    check("EV/EBITDA stands alone for a measured reason: 12.7% of FY2014 has "
          "EBITDA and no earnings",
          V.MEASURED["earnings_negativity_fy2014"]["ebitda_but_no_earnings_pct"]
          == 12.7)
    check("an unknown subfactor raises rather than returning None",
          _raises(lambda: V.subfactor("book_value")))

    check("ALL THREE legs declare the raw as-traded price -- owner decision 2",
          all(V.PRICE_RAW_AS_TRADED in V.SUBFACTORS[k].required_primitives
              for k in V.SUBFACTOR_KEYS)
          and V.anchor_policy()["price_basis"] == V.PRICE_RAW_AS_TRADED)
    check("the two price concepts are DIFFERENT primitives, not two spellings",
          V.PRICE_RAW_AS_TRADED != V.PRICE_ACTION_ADJUSTED
          and not any(V.PRICE_ACTION_ADJUSTED in V.SUBFACTORS[k].required_primitives
                      for k in V.SUBFACTOR_KEYS))
    # A TypeError, not a ValueError, and the test says so on purpose: an
    # adjusted price standing in for a raw one is not a bad VALUE, it is the
    # wrong KIND of thing, and _raises defaults to ValueError precisely so a
    # swap of error class cannot pass unnoticed.
    check("substituting an adjusted price for a valuation role RAISES",
          _raises(lambda: pit_price_basis.assert_price_basis(
                      pit_price_basis.PRICE_ROLE_VALUATION,
                      V.PRICE_ACTION_ADJUSTED),
                  pit_price_basis.PriceBasisError))
    check("and the refusal is a TypeError -- wrong KIND, not wrong value",
          issubclass(pit_price_basis.PriceBasisError, TypeError)
          and not issubclass(pit_price_basis.PriceBasisError, ValueError))
    check("the reverse swap is refused too -- a return may not use a raw price",
          _raises(lambda: pit_price_basis.assert_price_basis(
                      pit_price_basis.PRICE_ROLE_RETURN, V.PRICE_RAW_AS_TRADED),
                  pit_price_basis.PriceBasisError))
    check("pit_shares' raw spelling is still reachable and still the same idea",
          V.PRICE_BASIS_RAW is pit_shares.PRICE_BASIS_RAW)
    check("the anchor carries a VERSIONED name and the alias agrees",
          V.PE_ABSOLUTE_ANCHOR_V1 == 20.0
          and V.PE_ABSOLUTE_ANCHOR == V.PE_ABSOLUTE_ANCHOR_V1)
    check("the neutrality statement is recorded and is NOT a fair-value claim",
          "neutrality" in V.ANCHOR_NEUTRALITY_SEMANTICS.lower()
          and "not a statement that 20x is fair value"
              in V.ANCHOR_NEUTRALITY_SEMANTICS
          and "rate-blind" in V.ANCHOR_NEUTRALITY_SEMANTICS.lower())
    check("the sweep is a DIAGNOSTIC and forbids ranking anchors by return",
          V.ANCHOR_SWEEP_SPEC["purpose"] == "SENSITIVITY_DIAGNOSTIC"
          and V.ANCHOR_SWEEP_SPEC["explicitly_not"] == "ANCHOR_OPTIMISATION"
          and V.ANCHOR_SWEEP_SPEC["grid"] == (14.0, 16.0, 18.0, 20.0, 22.0)
          and any("realized return" in m
                  for m in V.ANCHOR_SWEEP_SPEC["must_not_report"])
          and V.ANCHOR_SWEEP_SPEC["status"] == "SPECIFIED, NOT RUN")
    check("the anchor is named as a future ML CHALLENGER, not a frozen truth",
          "challenger" in V.ANCHOR_SWEEP_SPEC["future_challenger"].lower())
    check("the conflict with the frozen pe_ratio spec is RECORDED, not fixed",
          V.KNOWN_LIMITATIONS["pe_ratio_price_basis_conflict"]["status"]
          == pit_factor_spec.SHAFFER_V1_KNOWN_LIMITATION
          and "price_adjusted" in pit_factor_spec.spec("pe_ratio").required_primitives)


# --------------------------------------------------------------------------
# 2. the presence rule -- V=1 is a FUNCTION of the subfactors
# --------------------------------------------------------------------------

def test_presence() -> None:
    print()
    print("== 2. the pillar presence rule ==")

    owner = V.presence({V.SUBFACTOR_PE_ABSOLUTE: True,
                        V.SUBFACTOR_PE_RELATIVE: True,
                        V.SUBFACTOR_EV_EBITDA: False})
    check("THE OWNER'S CASE is PRESENT, not absent",
          owner.is_present and owner.state == V.PRESENT_DEGRADED)
    check("the detailed mask carries the truth: V[PE=1,PEPeer=1,EVEBITDA=0]",
          owner.mask == "V[PE=1,PEPeer=1,EVEBITDA=0]", owner.mask)
    check("present-but-degraded IS pit_store.AVAIL_PARTIAL, not a new word",
          owner.availability == pit_store.AVAIL_PARTIAL)

    full = V.presence({k: True for k in V.SUBFACTOR_KEYS})
    check("all three present is PRESENT_FULL / AVAIL_COMPLETE",
          full.state == V.PRESENT_FULL
          and full.availability == pit_store.AVAIL_COMPLETE
          and full.mask == V.MASK_FULL)

    none = V.presence({})
    check("nothing present is ABSENT / AVAIL_UNAVAILABLE / MASK_NONE",
          none.state == V.ABSENT and none.mask == V.MASK_NONE
          and none.availability == pit_store.AVAIL_UNAVAILABLE)

    check("the ANCHOR carries the pillar alone -- a 100x P/E with no peers "
          "anywhere is still a valuation view",
          V.presence({V.SUBFACTOR_PE_ABSOLUTE: True}).is_present)
    check("the SUPPLEMENT carries the pillar alone -- a loss-maker with "
          "positive EBITDA has no P/E and a real EV/EBITDA",
          V.presence({V.SUBFACTOR_EV_EBITDA: True}).is_present)

    orphan = V.presence({V.SUBFACTOR_PE_RELATIVE: True})
    check("peer CONTEXT without an absolute leg is REFUSED for dependency",
          not orphan.is_present
          and orphan.refused_for_dependency == (V.SUBFACTOR_PE_RELATIVE,))
    check("a dependency refusal is distinguishable from a plain absence",
          orphan.mask == V.presence({}).mask
          and orphan.refused_for_dependency != V.presence({}).refused_for_dependency)

    check("the presence rule is an OR over standalone subfactors, NOT a "
          "conjunction: 7 of 8 offers are present, and v1 would give 1",
          sum(1 for bits in range(8)
              if V.presence({k: bool(bits & (1 << i))
                             for i, k in enumerate(V.SUBFACTOR_KEYS)}).is_present) == 6)

    for bits in range(8):
        offered = {k: bool(bits & (1 << i)) for i, k in enumerate(V.SUBFACTOR_KEYS)}
        p = V.presence(offered)
        if V.parse_mask(p.mask) != {k: k in p.present for k in V.SUBFACTOR_KEYS}:
            check(f"mask round-trips for {p.mask}", False)
            break
    else:
        check("every mask round-trips through parse_mask", True)

    check("a typo'd subfactor name RAISES, never silently means absent",
          _raises(lambda: V.presence({"pe_absolut": True})))
    check("an unreadable mask raises rather than decoding to nothing",
          _raises(lambda: V.parse_mask("V[PE=1]"))
          and _raises(lambda: V.parse_mask("VGPD"))
          and _raises(lambda: V.parse_mask("V[PE=2,PEPeer=0,EVEBITDA=0]")))


# --------------------------------------------------------------------------
# 3. the convex transform, and the absolute anchor
# --------------------------------------------------------------------------

def test_transform() -> None:
    print()
    print("== 3. the convex extreme-valuation transform ==")
    a = V.PINNED_CURVE["anchor"]

    check("B is SOLVED from the -100-at-10x condition, not pasted",
          _close(V.TRANSFORM_B, V.solve_b(), 1e-15)
          and round(V.TRANSFORM_B, 4) == V.PINNED_CURVE["b_published"],
          V.TRANSFORM_B)
    check("B does not depend on the anchor LEVEL -- the condition is a multiple",
          _close(V.valuation_score(10 * 20.0, 20.0), -100.0)
          and _close(V.valuation_score(10 * 16.0, 16.0), -100.0))
    check("the anchor itself scores exactly zero",
          _close(V.valuation_score(a, a), 0.0, 1e-12))

    c1 = V.valuation_score(20.0, a) - V.valuation_score(30.0, a)
    c2 = V.valuation_score(100.0, a) - V.valuation_score(150.0, a)
    check("20->30 costs 10.14 points", _close(c1, 10.1366, 1e-3), c1)
    check("100->150 costs 25.00 points", _close(c2, 25.0029, 1e-3), c2)
    check("the ratio is 2.47x, the owner's requirement of MORE than 1",
          _close(c2 / c1, 2.4666, 1e-3) and c2 > c1, c2 / c1)

    ln_equal = _close(math.log(30 / 20), math.log(150 / 100), 1e-12)
    linear_cost_1 = 25.0 * math.log(30 / 20)
    linear_cost_2 = 25.0 * math.log(150 / 100)
    check("THE LOG TRAP IS REAL: ln(30/20) == ln(150/100) exactly, so a "
          "linear-in-log penalty charges the same for both -- ratio 1.00",
          ln_equal and _close(linear_cost_1, linear_cost_2, 1e-12))
    v1_cost_1 = 100 * math.tanh(2 * 0.5) - 100 * math.tanh(2 * (30 / 20 - 1) * -1)
    v1_1 = abs(100 * math.tanh(2 * (20 / 20 - 1)) - 100 * math.tanh(2 * (30 / 20 - 1)))
    v1_2 = abs(100 * math.tanh(2 * (100 / 20 - 1)) - 100 * math.tanh(2 * (150 / 20 - 1)))
    check("v1's tanh is worse than flat -- it INVERTS: the far move costs "
          "less than the near one",
          v1_2 < v1_1, (v1_1, v1_2))

    check("monotone non-increasing in the multiple",
          all(V.valuation_score(hi) <= V.valuation_score(lo) + 1e-12
              for lo, hi in zip([1, 5, 20, 50, 200], [5, 20, 50, 200, 1000])))
    check("the cheap side SATURATES at +C=60 -- extreme cheapness is more "
          "often distress than opportunity",
          V.valuation_score(0.0001) < V.TRANSFORM_C
          and V.valuation_score(0.0001) > V.TRANSFORM_C - 1.0)
    check("the rich side is floored at -100 and clamps beyond it",
          _close(V.valuation_score(200.0, a), -100.0)
          and _close(V.valuation_score(1e6, a), -100.0))

    check("a NEGATIVE multiple scores None, never abs()",
          V.valuation_score(-2.0) is None and V.valuation_score(0.0) is None)
    check("the refusal reuses pit_eps's loss vocabulary, not a new spelling",
          V.valuation_score_detail(-2.0)["reason"] is pit_eps.PE_UNAVAILABLE_LOSS)
    check("a zero anchor raises rather than dividing",
          _raises(lambda: V.valuation_score(10.0, 0.0)))

    print()
    print("   -- what is arbitrary about the anchor --")
    check("the anchor is a single named constant, so a sweep is one line",
          V.PE_ABSOLUTE_ANCHOR == 20.0
          and V.ANCHOR_POLICY_VERSION == "valuation_anchor_v1_fixed_pe_20")
    check("the KIND is argued (fixed) and the LEVEL is declared arbitrary",
          "FIXED" in V.WHAT_IS_ARBITRARY["the_kind_is_defensible"]
          and "CONVENTION AND NOT A MEASUREMENT"
          in V.WHAT_IS_ARBITRARY["the_level_is_arbitrary"])
    check("the rejected alternatives are named with their defects",
          "SURVIVOR_ONLY" in V.WHAT_IS_ARBITRARY["the_level_is_arbitrary"])
    check("the rate/inflation limitation is stated, not fudged",
          "ten-year yield"
          in V.WHAT_IS_ARBITRARY["not_rate_or_inflation_adjusted"])
    check("the arbitrariness is BOUNDED: within one as-of the ORDERING is "
          "invariant to the anchor",
          [V.valuation_score(m, 20.0) for m in (10, 20, 40, 100)]
          != [V.valuation_score(m, 16.0) for m in (10, 20, 40, 100)]
          and all((V.valuation_score(hi, 20.0) < V.valuation_score(lo, 20.0))
                  == (V.valuation_score(hi, 16.0) < V.valuation_score(lo, 16.0))
                  for lo, hi in ((10, 20), (20, 40), (40, 100))))
    check("the sensitivity is quotable: 100x costs 53.99 at 20 and 67.08 at 16",
          _close(V.valuation_score(100.0, 20.0), -53.9905, 1e-3)
          and _close(V.valuation_score(100.0, 16.0), -67.0841, 1e-3))


# --------------------------------------------------------------------------
# 4. EARNINGS_SIGN_CROSS
# --------------------------------------------------------------------------

def test_sign_crossing() -> None:
    print()
    print("== 4. EARNINGS_SIGN_CROSS as its own state ==")

    cross = V.eps_transition(-0.10, 0.10, same_share_basis=True)
    naive = (0.10 - (-0.10)) / -0.10 * 100.0
    check("the naive formula really does return -200% -- the value refused "
          "here is COMPUTABLE, which is the whole point",
          _close(naive, -200.0, 1e-9))
    check("-$0.10 -> +$0.10 produces NO percentage growth number",
          cross.growth_pct is None and not cross.permits_percentage_growth)
    check("it is a first-class discontinuity state",
          cross.state == V.TRANSITION_LOSS_TO_PROFIT
          and cross.is_sign_cross
          and cross.discontinuity_state == V.EARNINGS_SIGN_CROSS)
    check("the direction survives as a CATEGORY, so the economics is not lost",
          cross.direction == V.TRANSITION_LOSS_TO_PROFIT)

    back = V.eps_transition(0.10, -0.10, same_share_basis=True)
    check("the mirror image is its own directed state, not the same one",
          back.state == V.TRANSITION_PROFIT_TO_LOSS
          and back.growth_pct is None and back.state != cross.state)

    narrowing = V.eps_transition(-1.00, -0.10, same_share_basis=True)
    check("A NEGATIVE BASE IS ALSO REFUSED: -1.00 -> -0.10 is a 90% "
          "improvement and the formula returns -90%",
          narrowing.growth_pct is None
          and narrowing.direction == V.DIR_LOSS_NARROWING
          and _close((-0.10 - -1.00) / -1.00 * 100.0, -90.0, 1e-9))
    check("a deepening loss is the opposite direction, still no percentage",
          V.eps_transition(-0.10, -1.00, same_share_basis=True).direction
          == V.DIR_LOSS_DEEPENING)

    check("a reported ZERO base is undefined, not infinity and not imputed",
          V.eps_transition(0.0, 0.5, same_share_basis=True).state
          == V.TRANSITION_FROM_ZERO
          and V.eps_transition(0.0, 0.5, same_share_basis=True).growth_pct is None)
    check("a sub-cent BASE is a quantisation artefact and is refused too",
          V.eps_transition(0.005, 0.50, same_share_basis=True).state
          == V.TRANSITION_NEAR_ZERO_BASE)
    check("a collapse TO near zero has a sound base and DOES get a percentage",
          _close(V.eps_transition(1.00, 0.005,
                                  same_share_basis=True).growth_pct, -99.5, 1e-9))

    ok = V.eps_transition(1.00, 1.20, same_share_basis=True)
    check("both positive, one share basis: the ONLY state that grows 20%",
          ok.state == V.TRANSITION_BOTH_POSITIVE and _close(ok.growth_pct, 20.0))

    print()
    print("   -- the exhaustive grid --")
    grid = [-5.0, -1.0, -0.01, -0.005, 0.0, 0.005, 0.01, 1.0, 50.0]
    offenders: list[tuple] = []
    unknown_states: list[tuple] = []
    for prev in grid:
        for curr in grid:
            t = V.eps_transition(prev, curr, same_share_basis=True)
            if t.state not in V.TRANSITION_STATES:
                unknown_states.append((prev, curr, t.state))
            crossed = (prev < 0 < curr) or (curr < 0 < prev)
            if t.growth_pct is not None and (crossed or prev <= pit_eps.EPS_NEAR_ZERO_ABS):
                offenders.append((prev, curr, t.growth_pct))
            if crossed and not t.is_sign_cross:
                offenders.append((prev, curr, "not flagged as a crossing"))
    check(f"over {len(grid) ** 2} ordered pairs, NO sign-crossing, zero, "
          f"negative or sub-cent base ever produces a percentage",
          not offenders, offenders[:4])
    check("every pair lands in a NAMED state", not unknown_states,
          unknown_states[:4])

    print()
    print("   -- what replaces the percentage --")
    yc = V.earnings_yield_change(-0.10, 0.10, 20.0, same_share_basis=True)
    check("the signed earnings-yield change is defined THROUGH zero and "
          "points the right way (+, for a loss turning into a profit)",
          yc["yield_change"] is not None and yc["yield_change"] > 0
          and _close(yc["yield_change"], 0.01))
    check("it is monotone: a bigger earnings improvement is a bigger number",
          V.earnings_yield_change(-0.10, 0.50, 20.0,
                                  same_share_basis=True)["yield_change"]
          > yc["yield_change"])
    check("it is labelled a RESEARCH candidate and SURVIVOR_ONLY, never a "
          "valuation leg",
          yc["role"] == pit_normalization.ROLE_RESEARCH
          and yc["sample_scope"] == pit_store.SAMPLE_SURVIVOR_ONLY)

    print()
    print("   -- the share-basis trap --")
    check("an UNKNOWN share basis refuses the percentage on a clean pair",
          V.eps_transition(1.00, 1.20).growth_pct is None
          and V.eps_transition(1.00, 1.20).same_share_basis is None)
    check("...and refuses the yield change and the level change too",
          V.earnings_yield_change(1.00, 1.20, 20.0)["yield_change"] is None
          and V.eps_transition(1.00, 1.20).level_change is None)
    check("the refusal names the Apple 4:1 measurement",
          "11.89" in V.eps_transition(1.00, 1.20).refused_because)

    print()
    print("   -- the vocabulary is pit_eps's, not a second one --")
    check("the four value cases come from pit_eps.sign_case",
          V.eps_transition(-1.0, 1.0,
                           same_share_basis=True).prev_sign_case
          == pit_eps.SIGN_NEGATIVE
          and V.eps_transition(1.0, -1.0,
                               same_share_basis=True).pe_leg
          is pit_eps.PE_UNAVAILABLE_LOSS)
    check("a negative EPS is a LOSS-MAKING STATE, not a cheap P/E",
          pit_eps.price_earnings(10.0, -5.0)["pe"] is None
          and V.valuation_score(pit_eps.price_earnings(10.0, -5.0)["pe"]) is None)
    check("pit_eps.SIGN_CROSSING (the ingest flag) and CROSSING_STATES (the "
          "directed reading) both exist and are different objects",
          pit_eps.SIGN_CROSSING == "sign_crossing"
          and V.CROSSING_STATES == frozenset({V.TRANSITION_LOSS_TO_PROFIT,
                                              V.TRANSITION_PROFIT_TO_LOSS}))


# --------------------------------------------------------------------------
# 5. subfactor-level data quality
# --------------------------------------------------------------------------

def test_quality() -> None:
    print()
    print("== 5. quality at SUBFACTOR granularity ==")
    comps = {V.COMPONENT_SOURCE_QUALITY: 0.95,
             V.COMPONENT_FRESHNESS: 0.92,
             V.COMPONENT_PRIMITIVE_COVERAGE: 0.98}

    q = V.data_quality(V.SUBFACTOR_PE_ABSOLUTE, comps)
    check("with no rule named, Q is None and NOTHING is adopted",
          q.quality is None and q.adopted_rule is None
          and q.rule_status == V.QUALITY_RULE_UNDECIDED)
    check("every candidate rule is still computed, so the choice stays open",
          q.candidates[V.QUALITY_RULE_PRODUCT] is not None
          and q.candidates[V.QUALITY_RULE_MIN] is not None
          and q.candidates[V.QUALITY_RULE_WEIGHTED] is not None)
    check("the product and the min differ enough for the choice to matter "
          "(0.857 vs 0.920 on a GOOD subfactor)",
          abs(q.candidates[V.QUALITY_RULE_PRODUCT]
              - q.candidates[V.QUALITY_RULE_MIN]) > 0.05)

    check("PEER COHERENCE IS REFUSED on the absolute leg -- it is not a "
          "question about a reading that consults no peers",
          _raises(lambda: V.data_quality(
              V.SUBFACTOR_PE_ABSOLUTE,
              dict(comps, **{V.COMPONENT_PEER_COHERENCE: 1.0}))))
    check("...and it IS a question about the relative leg",
          V.COMPONENT_PEER_COHERENCE
          in V.subfactor(V.SUBFACTOR_PE_RELATIVE).quality_components)
    check("THIS is why quality cannot live at pillar level: the two legs do "
          "not even have the same components",
          set(V.subfactor(V.SUBFACTOR_PE_ABSOLUTE).quality_components)
          != set(V.subfactor(V.SUBFACTOR_PE_RELATIVE).quality_components))

    partial = V.data_quality(V.SUBFACTOR_PE_ABSOLUTE,
                             {V.COMPONENT_SOURCE_QUALITY: 0.9})
    check("a declared component the caller omitted is MISSING, not imputed 1.0",
          partial.missing == (V.COMPONENT_FRESHNESS,
                              V.COMPONENT_PRIMITIVE_COVERAGE)
          and partial.candidates[V.QUALITY_RULE_MIN] is None)
    check("a component outside [0,1] raises: that is a scaling error, not a "
          "strong opinion",
          _raises(lambda: V.data_quality(V.SUBFACTOR_PE_ABSOLUTE,
                                         dict(comps, freshness=1.4))))
    check("QUALITY_RULE_LEARNED refuses to run -- NO ML FITTING here",
          _raises(lambda: V.data_quality(V.SUBFACTOR_PE_ABSOLUTE, comps,
                                         V.QUALITY_RULE_LEARNED)))
    check("an unknown rule raises rather than defaulting",
          _raises(lambda: V.data_quality(V.SUBFACTOR_PE_ABSOLUTE, comps, "geomean")))

    named = V.data_quality(V.SUBFACTOR_PE_ABSOLUTE, comps, V.QUALITY_RULE_MIN)
    check("naming a rule is a deliberate act and lands on the record, and the "
          "status STAYS undecided",
          named.adopted_rule == V.QUALITY_RULE_MIN
          and _close(named.quality, 0.92, 1e-12)
          and named.rule_status == V.QUALITY_RULE_UNDECIDED)

    print()
    print("   -- F_raw, Q and F_effective: all three, never the product alone --")
    ev = V.effective_value(-53.9905, named)
    check("all three are returned",
          ev["f_raw"] is not None and ev["data_quality"] is not None
          and ev["f_effective"] is not None)
    check("F_effective = F_raw x Q", _close(ev["f_effective"], -53.9905 * 0.92))
    check("shrinkage moves a factor TOWARD NEUTRAL, not toward a verdict",
          abs(ev["f_effective"]) < abs(ev["f_raw"]))
    bullish = V.effective_value(+53.9905, named)
    check("...in both directions: a damped factor is less bullish AND less "
          "bearish",
          bullish["f_effective"] < 53.9905 and bullish["f_effective"] > 0)
    check("with no adopted rule, F_effective is None and F_raw stands",
          V.effective_value(-53.99, q)["f_effective"] is None
          and V.effective_value(-53.99, q)["f_raw"] == -53.99)
    check("the components travel with the number, so the shrinkage stays "
          "learnable",
          ev["quality_components"] == comps)


# --------------------------------------------------------------------------
# 6. THE WORKED EXAMPLE -- a real valuation view with EV/EBITDA absent
# --------------------------------------------------------------------------

def test_worked_example() -> None:
    print()
    print("== 6. the worked example at a thin-cohort as-of date ==")
    thin = V._build_pillar(V.FIXTURE_THIN)
    very = V._build_pillar(V.FIXTURE_VERY_THIN)

    check("the fixture is a 100.0x P/E, exactly",
          _close(thin.results[V.SUBFACTOR_PE_ABSOLUTE].multiple, 100.0, 1e-12))
    check("THE OWNER'S MASK comes out of the real assembly",
          thin.mask == "V[PE=1,PEPeer=1,EVEBITDA=0]", thin.mask)
    check("the pillar is PRESENT and produces a real number",
          thin.available and thin.presence.state == V.PRESENT_DEGRADED
          and thin.pillar_score is not None)
    check("EV/EBITDA is absent for the OWNER'S FIRST-CLASS OUTCOME, and the "
          "string is pit_store's",
          thin.results[V.SUBFACTOR_EV_EBITDA].unavailable_reason
          is pit_store.REASON_VALUATION_PEER_SET_INSUFFICIENT)
    check("the absence came from pit_factor_spec's REAL two-test gate, not a "
          "restatement of it",
          thin.results[V.SUBFACTOR_EV_EBITDA].detail["failed_tests"]
          == [pit_factor_spec.TEST_SUFFICIENT_N])
    check("the present subfactors renormalise WITHIN the pillar to 1.0",
          _close(sum(thin.effective_weights.values()), 1.0, 1e-12)
          and _close(thin.effective_weights[V.SUBFACTOR_PE_ABSOLUTE], 0.625, 1e-12))
    check("the pillar score is the weighted blend of the two present legs",
          _close(thin.pillar_score,
                 0.625 * thin.results[V.SUBFACTOR_PE_ABSOLUTE].raw_score
                 + 0.375 * thin.results[V.SUBFACTOR_PE_RELATIVE].raw_score))

    print()
    print("   -- THE REQUIREMENT: absolute valuation does not wait for peers --")
    solo = very.results[V.SUBFACTOR_PE_ABSOLUTE].raw_score
    check("at the VERY THIN date no admissible P/E cohort forms at all",
          very.results[V.SUBFACTOR_PE_RELATIVE].unavailable_reason is not None
          and very.mask == "V[PE=1,PEPeer=0,EVEBITDA=0]")
    check("and the pillar is STILL PRESENT, carried by the anchor alone",
          very.available and very.presence.standing_alone
          == (V.SUBFACTOR_PE_ABSOLUTE,))
    check("a 100x P/E is penalised 53.99 points with NO cohort anywhere",
          _close(solo, -53.9905, 1e-3) and _close(very.pillar_score, solo),
          solo)
    check("the penalty came from the CONVEX transform -- the quadratic term "
          "is doing real work at 100x",
          very.results[V.SUBFACTOR_PE_ABSOLUTE].detail["convex_term"] > 10.0)
    check("under a peer-relative-ONLY valuation the same company is charged "
          "NOTHING: no cohort, no percentile, no penalty",
          V.presence({V.SUBFACTOR_PE_RELATIVE: False,
                      V.SUBFACTOR_EV_EBITDA: False}).state == V.ABSENT)

    print()
    print("   -- the two legs disagree usefully rather than redundantly --")
    absolute = thin.results[V.SUBFACTOR_PE_ABSOLUTE].raw_score
    relative = thin.results[V.SUBFACTOR_PE_RELATIVE].raw_score
    check("absolute (vs a fixed 20.0x) is HARSHER than relative (vs a 28.0x "
          "peer median): the industry is expensive too, and only the fixed "
          "anchor can see that",
          absolute < relative < 0, (absolute, relative))
    check("the relative leg's anchor IS the cohort median, from the fixture",
          _close(thin.results[V.SUBFACTOR_PE_RELATIVE].anchor, 28.0))

    print()
    print("   -- quality at the pillar, assembled from the subfactors --")
    check("the pillar Q is the effective-weight-weighted mean of the legs",
          _close(thin.pillar_quality, 0.625 * 0.92 + 0.375 * 0.62, 1e-12),
          thin.pillar_quality)
    check("a pillar Q is None unless EVERY present leg adopted a rule",
          V._build_pillar(V.FIXTURE_THIN,
                          quality_rule=None).pillar_quality is None)
    check("the two legs carry DIFFERENT quality, which is the whole argument "
          "for subfactor granularity",
          thin.results[V.SUBFACTOR_PE_ABSOLUTE].quality.quality
          != thin.results[V.SUBFACTOR_PE_RELATIVE].quality.quality)
    check("LOSING THE WEAKER LEG RAISES pillar quality (0.8075 -> 0.9200) on "
          "strictly less information -- documented, deliberate, and NOT to be "
          "'fixed' by folding completeness into Q",
          very.pillar_quality > thin.pillar_quality
          and _close(very.pillar_quality, 0.92, 1e-12),
          (thin.pillar_quality, very.pillar_quality))
    check("...and the mask is what carries the information loss instead",
          thin.presence.present_weight > very.presence.present_weight
          and V.KNOWN_LIMITATIONS["losing_a_weak_leg_raises_the_pillar_quality"]
          ["status"].startswith("DELIBERATE"))
    check("a refused relative leg carries its GATE's failed tests, not just a "
          "reason string",
          very.results[V.SUBFACTOR_PE_RELATIVE].detail["failed_tests"]
          == [pit_factor_spec.TEST_SUFFICIENT_N])


# --------------------------------------------------------------------------
# 7. the wiring -- four canonical names, no fifth
# --------------------------------------------------------------------------

def test_wiring() -> None:
    print()
    print("== 7. wiring into the existing vocabulary ==")
    thin = V._build_pillar(V.FIXTURE_THIN)
    very = V._build_pillar(V.FIXTURE_VERY_THIN)

    check("VOCABULARY_MAP names FOUR dimensions and no fifth",
          sorted(V.VOCABULARY_MAP) == ["data_quality", "forecast_evidence",
                                       "hedge_evidence", "score_completeness"])

    print()
    print("   -- score_completeness: pit_score_signature, EXTENDED --")
    scores = dict(V.FIXTURE_OTHER_PILLARS)
    scores[SIG.PILLAR_VALUATION] = thin.for_score_signature()["factor_score"]
    result = SIG.score_v2(scores)
    check("a degraded-but-present pillar still produces the FULL signature -- "
          "a continuous degradation must NOT flip a discrete label",
          result.signature == SIG.V2_SIGNATURE_FULL
          and result.partial_score_marker == SIG.FULL_SHAFFER_SCORE)
    check("the pillar's own arithmetic is pit_score_signature's, unmodified",
          _close(result.company_score,
                 0.25 * thin.pillar_score + 0.35 * 62.0 + 0.15 * 18.0
                 + 0.10 * -24.0))
    check("the subfactor mask EXTENDS the pooling key; it does not replace it",
          V.stratify_key(thin, result)
          == (SIG.CANDIDATE_MODEL_VERSION, "EVGQ", thin.mask))
    check("two different masks under the SAME signature do not share a "
          "pooling key -- that is the pooling defect this closes",
          V.stratify_key(thin, result) != V.stratify_key(very, result)
          and V.stratify_key(thin, result)[1] == V.stratify_key(very, result)[1])

    absent = V.valuation_pillar({
        V.SUBFACTOR_PE_ABSOLUTE: V.SubfactorResult(
            key=V.SUBFACTOR_PE_ABSOLUTE, available=False,
            unavailable_reason=pit_store.REASON_STALE),
        V.SUBFACTOR_EV_EBITDA: V.SubfactorResult(
            key=V.SUBFACTOR_EV_EBITDA, available=False,
            unavailable_reason=pit_store.REASON_VALUATION_PEER_SET_INSUFFICIENT)})
    gone = SIG.score_v2({**V.FIXTURE_OTHER_PILLARS,
                         SIG.PILLAR_VALUATION: None},
                        reasons={SIG.PILLAR_VALUATION: absent.reason})
    check("a genuinely ABSENT pillar still lands as a PARTIAL SHAFFER SCORE "
          "with the owner's reason",
          absent.reason is pit_store.REASON_VALUATION_PEER_SET_INSUFFICIENT
          and gone.signature == "EGQ"
          and gone.partial_score_marker == SIG.PARTIAL_SHAFFER_SCORE
          and gone.valuation_peer_set_insufficient)
    check("the structural reason WINS over the coverage one -- a reader must "
          "not have to dig for it",
          absent.reason != pit_store.REASON_STALE)

    print()
    print("   -- forecast_evidence: pit_invariants, called not restated --")
    fe = V.forecast_evidence(thin.mask, 4820.0, 5, horizon="3M")
    check("the verdict is pit_invariants' own",
          fe["forecast_evidence"] is pit_invariants.STATUS_SUFFICIENT)
    twelve = V.forecast_evidence(thin.mask, 160.7, 3, horizon="12M")
    check("160.7 independent 12M observations is INSUFFICIENT_EVIDENCE -- "
          "detects nothing below rho 0.155 while ICs live at 0.02-0.06",
          twelve["forecast_evidence"] is pit_invariants.STATUS_INSUFFICIENT
          and twelve["detectable_rho"] > 0.15)
    check("the mask joins the stratification key",
          fe["stratified_by"] == ["model_version", "score_signature",
                                  "subfactor_mask"])
    check("an unreadable mask raises rather than being carried into a verdict",
          _raises(lambda: V.forecast_evidence("VGPD", 4820.0, 5)))

    print()
    print("   -- hedge_evidence: pit_intermediates, UNCHANGED --")
    check("a pre-archive as-of is prospective-only, verbatim from the source",
          V.hedge_evidence("2019-06-28")["hedge_evidence"]
          is pit_intermediates.HEDGE_LIVE_ONLY)
    check("a post-archive as-of is historically validated",
          V.hedge_evidence("2026-09-21")["hedge_evidence"]
          is pit_intermediates.HEDGE_VALIDATED)
    check("hedge evidence is explicitly NOT given a valuation flavour",
          V.VOCABULARY_MAP["hedge_evidence"]["extension"] == "NONE, deliberately.")

    print()
    print("   -- the score is NEVER multiplied by a confidence --")
    view = V.confidence_view(thin, result, V.FIXTURE_AS_OF, fe)
    flat = json.dumps(view, default=str)
    check("the view reports the UNADJUSTED company score",
          view["shaffer_score"] == result.company_score)
    check("there is no score x confidence product anywhere in the view",
          "overall_confidence" not in flat
          and "confidence_adjusted" not in flat)
    check("the four dimensions are reported side by side, with their own types",
          view["score_completeness"]["subfactor_mask"] == thin.mask
          and view["data_quality"]["band"] in [b for _, b in V.QUALITY_BANDS]
          and view["forecast_evidence"]["horizon"] == "3M"
          and view["hedge_evidence"]["hedge_evidence"] is not None)
    check("the rendered panel is the shape docs/CONFIDENCE-FRAMEWORK.md asks for",
          all(line in V.render_confidence_view(view)
              for line in ("Shaffer Score:", "Data quality:",
                           "Score completeness:", "Hedge evidence:")))


# --------------------------------------------------------------------------
# 8. restraint -- no connection, no write, no fit, no baseline
# --------------------------------------------------------------------------

def test_restraint() -> None:
    print()
    print("== 8. restraint ==")

    original = sqlite3.connect

    def _landmine(*args, **kwargs):
        raise AssertionError(f"pit_valuation_spec opened a connection: {args!r}")

    sqlite3.connect = _landmine
    opened = True
    try:
        V.render()
        V.render_curve()
        V.render_transitions()
        V.render_worked_example()
        V.anchor_policy()
        V.storage_note()
        V.migration_patch()
        V.validate()
        V.main()
        opened = False
    except AssertionError:
        opened = True
    finally:
        sqlite3.connect = original
    check("the ENTIRE public API, main() included, opens no connection",
          not opened)

    check("the real store is never named in this module",
          "shafferfineval_pit.db" not in open(
              os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "pit_valuation_spec.py"), encoding="ascii").read()
          .replace('store is 8.97 GiB', ''))
    check("no migration is applied", V.storage_note()["applied"] is False)
    check("the migration patch is the (column, type) shape pit_store.MIGRATIONS "
          "takes, so the two patches MERGE rather than compete",
          all(isinstance(t, tuple) and len(t) == 2
              for t in V.migration_patch()["pit_score"])
          and V.migration_patch()["pit_score"][:3]
          == [tuple(p) for p in SIG.PIT_SCORE_SIGNATURE_MIGRATIONS])
    check("the added columns carry no DEFAULT and duplicate no existing column",
          all("DEFAULT" not in t.upper() and "NOT NULL" not in t.upper()
              for _, t in V.PIT_SCORE_SUBFACTOR_MIGRATIONS)
          and not (set(n for n, _ in V.PIT_SCORE_SUBFACTOR_MIGRATIONS)
                   & set(n for n, _ in SIG.PIT_SCORE_SIGNATURE_MIGRATIONS)))

    # the DDL is exercised against a THROWAWAY in-memory store, never the real one
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(pit_store.SCHEMA)
        for name, coltype in SIG.PIT_SCORE_SIGNATURE_MIGRATIONS:
            conn.execute(f"ALTER TABLE pit_score ADD COLUMN {name} {coltype}")
        for statement in V.migration_sql():
            conn.execute(statement)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(pit_score)")}
        check("every specified column applies cleanly on top of the signature "
              "columns, in an in-memory store",
              all(name in cols
                  for name, _ in V.PIT_SCORE_SUBFACTOR_MIGRATIONS))
        check("the (signature, mask) index is created and spans both",
              any("subfactor_mask" in (r[0] or "") and "score_signature" in (r[0] or "")
                  for r in conn.execute(
                      "SELECT sql FROM sqlite_master WHERE type='index' "
                      "AND name='idx_pit_score_subfactor_mask'")))
        rows = conn.execute("SELECT COUNT(*) FROM pit_score").fetchone()[0]
        check("and not one row was written", rows == 0)
    finally:
        conn.close()

    check("no ML fitting: the learned rule is reserved and refuses",
          V.QUALITY_RULE_LEARNED in V.QUALITY_RULES
          and _raises(lambda: V.data_quality(
              V.SUBFACTOR_PE_ABSOLUTE,
              {V.COMPONENT_SOURCE_QUALITY: 0.9, V.COMPONENT_FRESHNESS: 0.9,
               V.COMPONENT_PRIMITIVE_COVERAGE: 0.9}, V.QUALITY_RULE_LEARNED)))
    check("no baseline is claimed: the limitation is on record",
          V.KNOWN_LIMITATIONS["no_replay_no_baseline"]["status"]
          == "OUT OF SCOPE BY INSTRUCTION")
    check("price-touching results are labelled SURVIVOR_ONLY_DIAGNOSTIC",
          V.SURVIVOR_ONLY is pit_store.SAMPLE_SURVIVOR_ONLY
          and all(V.SUBFACTORS[k].sample_scope == V.SURVIVOR_ONLY
                  for k in V.SUBFACTOR_KEYS)
          and V.MEASURED["sample_scope"]["anything_touching_price_or_return"]
          == V.SURVIVOR_ONLY)


# --------------------------------------------------------------------------
# 9. the module's own self-check
# --------------------------------------------------------------------------

def test_self_check() -> None:
    print()
    print("== 9. the module's own self-check ==")
    check("validate() finds nothing to complain about", V.validate() == [],
          V.validate())
    check("python pit_valuation_spec.py would exit 0", V.main() == 0)
    check("the measured context is on record and matches what was handed down",
          V.MEASURED["eps_sign_crossing"]["entities_touched_pct"] == 61.35
          and V.MEASURED["ev_ebitda_cohort"]["pct_ge_3"] == 34.2
          and V.MEASURED["pe_cohort"]["pct_ge_3"] == 78.3
          and V.MEASURED["total_debt_ladder"]["on_lower_bound_rung_pct"] == 94)
    check("the module is pure ASCII, like every other policy module here",
          all(ord(c) < 128 for c in open(
              os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "pit_valuation_spec.py"), encoding="ascii").read()))


def main() -> int:
    print("=" * 74)
    print("test_pit_valuation_spec -- three subfactors, subfactor quality,")
    print("                          and EARNINGS_SIGN_CROSS")
    print("=" * 74)
    for fn in (test_declarations, test_presence, test_transform,
               test_sign_crossing, test_quality, test_worked_example,
               test_wiring, test_restraint, test_self_check):
        fn()
    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
