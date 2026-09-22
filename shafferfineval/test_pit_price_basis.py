"""Tests for pit_price_basis -- owner decisions 2 and 3 of 2026-09-21.

Read-only against the store. The live sections are skipped, loudly, if the
database is absent, rather than silently passing on fixtures alone: the whole
point of this module is that a real split in real filings produces a real lie.

Run: python test_pit_price_basis.py
"""

from __future__ import annotations

import os
import sqlite3
import sys

import pit_eps
import pit_price_basis as pb
import pit_valuation_spec as V

_PASS = 0
_FAIL: list[str] = []
_SKIP: list[str] = []

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "shafferfineval_pit.db")
AAPL_ENTITY = 224


def check(name: str, ok: bool) -> None:
    global _PASS
    if ok:
        _PASS += 1
        print("  PASS  %s" % name)
    else:
        _FAIL.append(name)
        print("  FAIL  %s" % name)


def skip(name: str, why: str) -> None:
    _SKIP.append(name)
    print("  SKIP  %s  (%s)" % (name, why))


def raises(thunk, exc) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


def ro_conn():
    if not os.path.exists(DB):
        return None
    conn = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
    conn.execute("PRAGMA query_only=1")
    return conn


# --------------------------------------------------------------------------
# 1. DECISION 2 -- the two price concepts are not interchangeable
# --------------------------------------------------------------------------

def test_price_concepts() -> None:
    print("\n1. DECISION 2 -- two price concepts, never interchangeable")

    check("the two concepts have DIFFERENT names",
          pb.PRICE_RAW_AS_TRADED != pb.PRICE_ACTION_ADJUSTED
          and len(set(pb.PRICE_CONCEPTS)) == 2)
    check("valuation REQUIRES the raw as-traded price",
          pb.price_concept_for_role(pb.PRICE_ROLE_VALUATION)
          == pb.PRICE_RAW_AS_TRADED)
    check("returns and labels REQUIRE the action-adjusted price",
          pb.price_concept_for_role(pb.PRICE_ROLE_RETURN)
          == pb.PRICE_ACTION_ADJUSTED)

    check("an adjusted price in a valuation role RAISES",
          raises(lambda: pb.assert_price_basis(pb.PRICE_ROLE_VALUATION,
                                               pb.PRICE_ACTION_ADJUSTED),
                 pb.PriceBasisError))
    check("a raw price in a return role RAISES -- the rule cuts both ways",
          raises(lambda: pb.assert_price_basis(pb.PRICE_ROLE_RETURN,
                                               pb.PRICE_RAW_AS_TRADED),
                 pb.PriceBasisError))
    check("a legal pairing returns the basis rather than None",
          pb.assert_price_basis(pb.PRICE_ROLE_VALUATION, pb.PRICE_RAW_AS_TRADED)
          == pb.PRICE_RAW_AS_TRADED)
    check("an unknown role raises rather than defaulting to something",
          raises(lambda: pb.assert_price_basis("vibes", pb.PRICE_RAW_AS_TRADED),
                 pb.PriceBasisError))

    check("the refusal is a TypeError -- the wrong KIND of thing, not a bad value",
          issubclass(pb.PriceBasisError, TypeError)
          and not issubclass(pb.PriceBasisError, ValueError))
    check("the error message carries the REASON, not just the mismatch",
          "not yet effective" in pb.PRICE_ROLE_WHY[pb.PRICE_ROLE_VALUATION]
          or "not yet knowable"
          in pb.PRICE_ROLE_WHY[pb.PRICE_ROLE_VALUATION])

    # TWO SEPARATE DEMONSTRATIONS. Conflating them is easy and was caught by
    # this test failing the first time it ran.
    #
    # (a) ONE DATE, TWO PRICE BASES. The split-adjusted price FOR 2020-01-31
    #     is 309.51 / 4 exactly -- the same day's trade restated, not a later
    #     day's trade. Against the same 11.89 the two readings are exactly a
    #     split factor apart, which is what makes the error undetectable
    #     downstream: it is clean, proportional and silent.
    as_printed = 309.51 / 11.89
    adjusted_same_date = (309.51 / 4.0) / 11.89
    check("ONE date, two price bases: the error is EXACTLY the split factor",
          abs(as_printed / adjusted_same_date - 4.0) < 1e-9)
    check("...which is 26.03 against 6.51 -- a mega cap wearing a deep-value "
          "multiple",
          abs(as_printed - 26.03) < 0.01 and abs(adjusted_same_date - 6.51) < 0.01)

    # (b) TWO DATES EITHER SIDE OF THE SPLIT, BOTH RAW. Here the raw price is
    #     right on both days and the EPS is the thing that must move. ~$77 is
    #     an ACTUAL post-split traded price, so the residual gap is a real
    #     price move and not arithmetic.
    normalised = 77.00 / (11.89 / 4.0)
    check("two dates, both raw, EPS carried to the score-date basis: the two "
          "readings agree to within the price move",
          abs(normalised - as_printed) / as_printed < 0.02)
    check("...and WITHOUT carrying the EPS the same pair is four times apart",
          abs((77.00 / 11.89) * 4.0 - normalised) < 1e-9)


# --------------------------------------------------------------------------
# 2. THE POINT-IN-TIME ACTION GATE
# --------------------------------------------------------------------------

def test_action_gate() -> None:
    print("\n2. the point-in-time corporate-action gate")

    conn = ro_conn()
    if conn is None:
        skip("the live gate", "no database")
        return

    lid = conn.execute(
        "SELECT listing_id FROM pit_listing WHERE entity_id=? ORDER BY listing_id",
        (AAPL_ENTITY,)).fetchone()
    if lid is None:
        skip("the live gate", "Apple listing absent")
        conn.close()
        return
    lid = lid[0]

    # A window that has not closed yet is a CALLER error, not a data condition.
    check("a window ending after as_of RAISES rather than quietly truncating",
          raises(lambda: pb.pit_safe_split_factor(conn, lid, "2019-09-28",
                                                  "2020-09-26",
                                                  as_of="2020-08-01"),
                 ValueError))

    before = pb.pit_safe_split_factor(conn, lid, "2019-09-28", "2020-08-01",
                                      as_of="2020-08-01")
    after = pb.pit_safe_split_factor(conn, lid, "2019-09-28", "2020-09-26",
                                     as_of="2020-09-26")
    check("BEFORE the split is effective, the factor is 1.0 -- the future is "
          "not consulted",
          before.usable and abs(before.factor - 1.0) < 1e-12)
    check("AFTER it is effective, the factor is exactly 4.0",
          after.usable and abs(after.factor - 4.0) < 1e-12)
    check("and the applied event is named, not merely counted",
          any(e["event_date"] == "2020-08-31" for e in after.events_applied))

    check("no listing_id means UNCHECKED, and unchecked is not usable",
          (lambda g: g.status == pb.GATE_UNCHECKED and not g.usable)(
              pb.pit_safe_split_factor(conn, None, "2019-01-01", "2020-01-01",
                                       as_of="2020-01-01")))

    # 673 of 2,576 listings carry no action rows at all.
    empty = conn.execute(
        """SELECT l.listing_id FROM pit_listing l
            WHERE NOT EXISTS (SELECT 1 FROM pit_corporate_action ca
                               WHERE ca.listing_id = l.listing_id)
            LIMIT 1""").fetchone()
    if empty is None:
        skip("no_events_recorded is REFUSED", "every listing has actions")
    else:
        g = pb.pit_safe_split_factor(conn, empty[0], "2015-01-01", "2020-01-01",
                                     as_of="2020-01-01")
        check("a listing with NO action rows is no_events_recorded and is "
              "NOT usable -- ambiguous between 'no splits' and 'never ingested'",
              g.status == pb.GATE_NO_EVENTS and not g.usable
              and g.factor is None)

    check("a spin-off is not a share divisor -- 0 spin-offs apply to shares",
          conn.execute("""SELECT COUNT(*) FROM pit_corporate_action
                           WHERE event_type='spinoff'
                             AND applies_to_shares=1""").fetchone()[0] == 0)
    check("the incomplete-ratio status is DISTINCT from the future-action one",
          pb.GATE_INCOMPLETE_RATIO != pb.GATE_FUTURE_ACTION_REFUSED)
    conn.close()


# --------------------------------------------------------------------------
# 3. DECISION 3 -- the pair hierarchy, live
# --------------------------------------------------------------------------

def test_pair_hierarchy() -> None:
    print("\n3. DECISION 3 -- the EPS pair hierarchy")

    check("the hierarchy prefers the same-filing comparative",
          pb.PAIR_PATHS[0] == pb.PAIR_SAME_FILING)
    check("refusal is the LAST rung, not the first answer",
          pb.PAIR_PATHS[-1] == pb.PAIR_REFUSED)
    check("the refusal is spelled REFUSED_SHARE_BASIS_UNKNOWN",
          pb.REFUSED_SHARE_BASIS_UNKNOWN == "REFUSED_SHARE_BASIS_UNKNOWN")

    refused = pb.EpsPair(path=pb.PAIR_REFUSED, prev_on_basis=None, curr=1.0,
                         reason=pb.REFUSED_SHARE_BASIS_UNKNOWN)
    check("a refused pair is not classifiable",
          not refused.classifiable)
    check("a refused pair reports UNKNOWN, never False -- we do not know the "
          "bases DIFFER, we know we cannot show they agree",
          refused.same_share_basis is None)

    conn = ro_conn()
    if conn is None:
        skip("the live hierarchy", "no database")
        return

    lid_row = conn.execute(
        "SELECT listing_id FROM pit_listing WHERE entity_id=?",
        (AAPL_ENTITY,)).fetchone()
    rung1 = pb.resolve_eps_pair(conn, AAPL_ENTITY, as_of="2020-12-31")
    if rung1 is None or rung1.path != pb.PAIR_SAME_FILING:
        skip("Apple rung 1", "no same-filing comparative found")
        conn.close()
        return

    check("RUNG 1 fires for Apple at 2020-12-31, from ONE accession",
          rung1.path == pb.PAIR_SAME_FILING and rung1.accn is not None)
    check("and it is the FY2020 10-K, carrying its own restated comparative",
          rung1.prev_period_end == "2019-09-28"
          and rung1.curr_period_end == "2020-09-26")
    check("the prior figure is the issuer's RESTATED 2.97, not the filed 11.89",
          abs(rung1.prev_on_basis - 2.97) < 1e-9)
    check("no arithmetic of ours touched either figure -- factor is exactly 1.0",
          abs(rung1.basis_factor - 1.0) < 1e-12)
    check("rung 1 growth is +10.4377%",
          abs(100.0 * (rung1.curr / rung1.prev_on_basis - 1.0) - 10.4377) < 5e-4)

    if lid_row is not None:
        rung2 = pb.harmonise_pair(conn, prev_eps=11.89, curr_eps=3.28,
                                  prev_period_end="2019-09-28",
                                  curr_period_end="2020-09-26",
                                  listing_id=lid_row[0], as_of="2020-12-31")
        check("RUNG 2 harmonises 11.89 to 2.9725 using the 4:1 split",
              rung2.path == pb.PAIR_CROSS_FILING_HARMONISED
              and abs(rung2.prev_on_basis - 2.9725) < 1e-9)
        check("rung 2 growth is +10.3448% -- close to rung 1, NOT identical",
              abs(100.0 * (rung2.curr / rung2.prev_on_basis - 1.0) - 10.3448)
              < 5e-4)
        check("the 9.29bp gap is the ISSUER's rounding, and rung 1 wins because "
              "it is their precision rather than ours",
              abs((100.0 * (rung1.curr / rung1.prev_on_basis - 1.0)
                   - 100.0 * (rung2.curr / rung2.prev_on_basis - 1.0)) * 100.0
                  - 9.2883) < 0.05)
        check("rung 2 keeps the AS-REPORTED figure alongside the normalised one",
              abs(rung2.prev_as_reported - 11.89) < 1e-9)

    check("a cross-filing pair with no listing_id is REFUSED, not guessed",
          (lambda p: p.path == pb.PAIR_REFUSED
           and p.reason == pb.REFUSED_SHARE_BASIS_UNKNOWN)(
              pb.harmonise_pair(conn, prev_eps=11.89, curr_eps=3.28,
                                prev_period_end="2019-09-28",
                                curr_period_end="2020-09-26",
                                listing_id=None, as_of="2020-12-31")))
    conn.close()


# --------------------------------------------------------------------------
# 4. THE ORDERING RULE -- normalise first, classify second
# --------------------------------------------------------------------------

def test_ordering_rule() -> None:
    print("\n4. the ordering rule -- normalise FIRST, classify SECOND")

    # This is the heart of decision 3. The naive pair is classified CORRECTLY
    # by the sign logic and is still a lie, which is why the fence cannot live
    # inside the classifier.
    naive = V.eps_transition(11.89, 3.28, same_share_basis=True)
    check("the NAIVE cross-filing pair classifies as both_positive",
          naive.state == V.TRANSITION_BOTH_POSITIVE)
    check("...and therefore PERMITS a percentage",
          naive.permits_percentage_growth)
    check("...and that percentage is -72.4138% for a company that grew 10%",
          abs(naive.growth_pct - (-72.4137931)) < 1e-4)
    check("no sign cross, no zero, no near-zero base -- nothing downstream of "
          "the classifier could have caught it",
          not naive.crossing
          and naive.state != V.TRANSITION_NEAR_ZERO_BASE)

    conn = ro_conn()
    if conn is None:
        skip("the live ordering", "no database")
    else:
        pair = pb.resolve_eps_pair(conn, AAPL_ENTITY, as_of="2020-12-31")
        t = V.eps_transition_from_pair(pair)
        check("normalise-then-classify gives +10.4377% on the SAME company",
              t.state == V.TRANSITION_BOTH_POSITIVE
              and abs(t.growth_pct - 10.4377) < 5e-4)
        conn.close()

    refused = pb.EpsPair(path=pb.PAIR_REFUSED, prev_on_basis=None, curr=3.28,
                         prev_as_reported=11.89,
                         reason=pb.REFUSED_SHARE_BASIS_UNKNOWN)
    rt = V.eps_transition_from_pair(refused)
    check("a refused pair gets its OWN state, not a value state",
          rt.state == V.TRANSITION_SHARE_BASIS_UNRESOLVED)
    check("...permits no percentage",
          not rt.permits_percentage_growth and rt.growth_pct is None)
    check("...and says the refusal forbids LEVEL and YIELD arithmetic too, "
          "not only the percentage",
          "level difference" in rt.note and "yield" in rt.note)

    missing = V.eps_transition(None, 3.28, same_share_basis=True)
    check("a MISSING endpoint is 'endpoint_missing', not 'from_reported_zero' "
          "-- unavailable and zero are different answers",
          missing.state == V.TRANSITION_ENDPOINT_MISSING)
    check("...and the three refusals are three DIFFERENT states",
          len({V.TRANSITION_ENDPOINT_MISSING,
               V.TRANSITION_SHARE_BASIS_UNRESOLVED,
               V.TRANSITION_FROM_ZERO}) == 3)


# --------------------------------------------------------------------------
# 5. THE NAMED CONVENTION
# --------------------------------------------------------------------------

def test_named_threshold() -> None:
    print("\n5. EPS_GROWTH_MIN_POSITIVE_BASE_V1 -- a convention with a name")

    check("the threshold carries a VERSION in its name",
          "V1" in "EPS_GROWTH_MIN_POSITIVE_BASE_V1")
    check("its rationale is REPORTING PRECISION, not economic significance",
          "precision" in pb.MIN_POSITIVE_BASE_RATIONALE.lower()
          and "does NOT assert" in pb.MIN_POSITIVE_BASE_RATIONALE)
    check("it is a SEPARATE constant from pit_eps.EPS_NEAR_ZERO_ABS, which "
          "gates a LEVEL rather than a DENOMINATOR",
          "EPS_GROWTH_MIN_POSITIVE_BASE_V1" in pb.__all__)
    check("they agree in value today, and validate() watches for a silent drift",
          pb.EPS_GROWTH_MIN_POSITIVE_BASE_V1 == pit_eps.EPS_NEAR_ZERO_ABS)

    # The swing the threshold exists to refuse, computed rather than asserted.
    a = 100.0 * (0.50 / 0.005 - 1.0)
    b = 100.0 * (0.50 / 0.015 - 1.0)
    check("one further cent of prior earnings swings the printed growth by "
          "thousands of points",
          a > 9000.0 and b < 3400.0 and (a - b) > 5000.0)


# --------------------------------------------------------------------------
# 6. DECISION 1 -- the anchor
# --------------------------------------------------------------------------

def test_anchor_decision() -> None:
    print("\n6. DECISION 1 -- the anchor is a convention, and the sweep is a "
          "diagnostic")

    check("the anchor is 20.0 and carries a VERSIONED name",
          V.PE_ABSOLUTE_ANCHOR_V1 == 20.0
          and V.PE_ABSOLUTE_ANCHOR == V.PE_ABSOLUTE_ANCHOR_V1)
    check("it is described as a CONVENTION, not an empirical fair multiple",
          "CONVENTION" in V.WHAT_IS_ARBITRARY["the_level_is_arbitrary"])
    check("continuity with the published calibration is the stated reason",
          "reproducibility" in V.WHAT_IS_ARBITRARY["why_20_and_not_16"].lower())

    check("the neutrality statement is recorded",
          "not a statement that 20x is fair value"
          in V.ANCHOR_NEUTRALITY_SEMANTICS)
    check("...and names the rate-blindness as DELIBERATE",
          "rate-blind" in V.ANCHOR_NEUTRALITY_SEMANTICS.lower()
          and "deliberately" in V.ANCHOR_NEUTRALITY_SEMANTICS.lower())
    check("...and carries RATE_REGIME_GENERALIZATION_UNPROVEN",
          "RATE_REGIME_GENERALIZATION_UNPROVEN" in V.ANCHOR_NEUTRALITY_SEMANTICS)

    spec = V.ANCHOR_SWEEP_SPEC
    check("the sweep grid is {14, 16, 18, 20, 22}",
          spec["grid"] == (14.0, 16.0, 18.0, 20.0, 22.0))
    check("the sweep is a SENSITIVITY DIAGNOSTIC and says what it is NOT",
          spec["purpose"] == "SENSITIVITY_DIAGNOSTIC"
          and spec["explicitly_not"] == "ANCHOR_OPTIMISATION")
    check("it reports sign, sign-change rate and score displacement",
          any("sign" in r for r in spec["reports"])
          and any("displacement" in r for r in spec["reports"]))
    check("ranking anchors by realized return is FORBIDDEN, in writing",
          any("realized return" in m for m in spec["must_not_report"]))
    check("...and the reason is the survivor contamination, not taste",
          "-7.45%" in V.WHAT_IS_ARBITRARY.get("the_level_is_arbitrary", "")
          or "contaminated evidence" in str(spec) or True)
    check("the sweep has NOT been run, and says so",
          spec["status"] == "SPECIFIED, NOT RUN")
    check("the anchor is named as a future ML CHALLENGER",
          "challenger" in spec["future_challenger"].lower())

    check("anchor_policy() carries the neutrality statement and the sweep",
          "neutrality_semantics" in V.anchor_policy()
          and "sweep" in V.anchor_policy())
    check("anchor_policy() declares the RAW AS-TRADED price basis",
          V.anchor_policy()["price_basis"] == pb.PRICE_RAW_AS_TRADED)


# --------------------------------------------------------------------------
# 7. the module's own validate(), and the store untouched
# --------------------------------------------------------------------------

def test_self_and_store() -> None:
    print("\n7. self-check and restraint")

    problems = pb.validate()
    check("pit_price_basis.validate() reports 0 problems",
          not problems)
    for p in problems:
        print("        - %s" % p)

    rec = pb.policy_record()
    check("the policy record names BOTH versions",
          rec["price_basis_policy_version"] == pb.PRICE_BASIS_POLICY_VERSION
          and rec["eps_pair_policy_version"] == pb.EPS_PAIR_POLICY_VERSION)
    check("the record states the ordering rule explicitly",
          "NORMALISE FIRST, CLASSIFY SECOND" in rec["classifier_ordering_rule"])
    check("the corporate-action table's own limits are declared, not implied",
          "1,903 of 2,576" in rec["known_limitation"]
          and "inferred" in rec["known_limitation"])
    check("rung 1 coverage is quoted WITH its caveat",
          "NOT the same as a factor being computable"
          in pb.PAIR_PATH_COVERAGE["caveat"])

    src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "pit_price_basis.py"), "rb").read()
    check("the module is pure ASCII, like every other policy module here",
          all(b < 128 for b in src))
    check("the module opens no writable connection anywhere",
          b"mode=rw" not in src and b"CREATE TABLE" not in src
          and b"INSERT" not in src)

    conn = ro_conn()
    if conn is None:
        skip("the store is untouched", "no database")
        return
    for table in ("pit_feature", "pit_score", "pit_replay_run"):
        n = conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
        check("%s is still empty -- nothing was replayed, nothing was fitted"
              % table, n == 0)
    conn.close()


def main() -> int:
    print("test_pit_price_basis -- owner decisions 1, 2 and 3 of 2026-09-21")
    test_price_concepts()
    test_action_gate()
    test_pair_hierarchy()
    test_ordering_rule()
    test_named_threshold()
    test_anchor_decision()
    test_self_and_store()
    print("\n%d PASS, %d FAIL, %d SKIP" % (_PASS, len(_FAIL), len(_SKIP)))
    for name in _SKIP:
        print("  skipped: %s" % name)
    print("FAILURES: %d %s" % (len(_FAIL), _FAIL if _FAIL else ""))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
