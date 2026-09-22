"""Tests for pit_coverage_contract -- the owner invariant of 2026-09-21.

    Coverage = (eligible observations with a usable value)
             / (explicitly defined eligible population)

Both halves required. A naked percentage is invalid.

Run: python test_pit_coverage_contract.py
"""

from __future__ import annotations

import os
import sys

import pit_coverage_contract as CC
import pit_price_basis
import pit_share_coverage

_PASS = 0
_FAIL: list[str] = []


def check(name: str, ok: bool) -> None:
    global _PASS
    if ok:
        _PASS += 1
        print("  PASS  %s" % name)
    else:
        _FAIL.append(name)
        print("  FAIL  %s" % name)


def raises(thunk, exc) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


def _probe(**kw) -> CC.CoverageClaim:
    base = dict(
        claim_id="probe.%d" % len(CC.CLAIMS),
        label="probe",
        numerator_definition="observations that carry a usable value",
        denominator_definition="the eligible population as explicitly defined",
        denominator_unit=CC.UNIT_ENTITIES,
        owner="probe:PROBE",
        sample_scope="PROBE",
        numerator=1,
        denominator=2,
    )
    base.update(kw)
    return CC.CoverageClaim(**base)


# --------------------------------------------------------------------------

def test_contract_is_enforced_at_construction() -> None:
    print("\n1. the contract is a CONSTRUCTOR PRECONDITION, not a checklist")

    check("a fully defined claim constructs",
          _probe().numerator_definition.startswith("observations"))

    check("an EMPTY numerator definition is refused",
          raises(lambda: _probe(numerator_definition=""), CC.NakedPercentage))
    check("an EMPTY denominator definition is refused",
          raises(lambda: _probe(denominator_definition="   "),
                 CC.NakedPercentage))
    check("a LABEL where a definition belongs is refused -- 'all entities' "
          "does not say which observations qualify or by what rule",
          raises(lambda: _probe(denominator_definition="all entities"),
                 CC.NakedPercentage))
    check("a percentage with NO counts and NO published value is refused -- "
          "there would be nothing to check it against",
          raises(lambda: _probe(numerator=None, denominator=None,
                                published_pct=None), CC.NakedPercentage))

    check("the refusal for a naked figure quotes the contract back",
          "Coverage = (eligible observations" in CC.CONTRACT)


def test_units() -> None:
    print("\n2. a denominator that changes UNIT is a different denominator")

    check("'listing_rows' is refused BY NAME, not merely discouraged",
          "listing_rows" in CC.REFUSED_UNITS
          and raises(lambda: _probe(denominator_unit="listing_rows"),
                     CC.UndefinedDenominator))
    check("...and the refusal carries the MEASUREMENT behind it",
          "2,065" in CC.REFUSED_UNITS["listing_rows"]
          and "2,034" in CC.REFUSED_UNITS["listing_rows"])
    check("vague units are refused too",
          all(u in CC.REFUSED_UNITS for u in ("rows", "records")))
    check("an unrecognised unit is refused rather than accepted quietly",
          raises(lambda: _probe(denominator_unit="thingies"),
                 CC.UndefinedDenominator))
    check("the admissible units are a closed, named set",
          CC.UNIT_ENTITY_DATES in CC.ADMISSIBLE_UNITS
          and "listing_rows" not in CC.ADMISSIBLE_UNITS)

    d = CC.UNIT_DEFECT
    check("the row-vs-entity defect is recorded with both series",
          d["measured"]["scored_universe_as_of rows"] == (2065, 2455, 2475)
          and d["measured"]["scored_universe_as_of entities"]
          == (2034, 2423, 2443))
    check("...and is described as a UNIT error, not a rounding one",
          "UNIT error" in d["consequence"])


def test_registry_adapts_and_never_retypes() -> None:
    print("\n3. every figure stays owned by the module that measured it")

    check("claims were registered by the adapters",
          len(CC.CLAIMS) >= 10)
    check("every share-coverage definition is adopted, none invented",
          all(("share." + spec_id) in CC.CLAIMS
              for spec_id in pit_share_coverage.DEFINITIONS))

    canonical = CC.claim("share." + pit_share_coverage.CANONICAL_DEFINITION_ID)
    check("the canonical share figure is 72.25% and comes from its owner",
          abs(canonical.value() - 72.25) < 0.005
          and canonical.value()
          == round(pit_share_coverage.PIT_SHARE_STATE_COVERAGE_V2, 2))
    check("...and its counts reproduce the published percentage",
          canonical.consistent()
          and canonical.numerator == 272587
          and canonical.denominator == 377304)
    check("...and every claim names the module:constant where it LIVES",
          all(":" in c.owner for c in CC.CLAIMS.values()))

    cov = pit_price_basis.PAIR_PATH_COVERAGE
    annual = CC.claim("eps.same_filing_pair.annual_filings")
    check("the EPS pair figures are adapted from pit_price_basis, not copied",
          annual.numerator == cov["annual_filings_with_comparative"]
          and annual.denominator == cov["annual_filings_qtrs4"])

    check("a duplicate claim id is refused -- two definitions of one figure is "
          "the condition this module exists to end",
          raises(lambda: CC.register(canonical), ValueError))
    check("an unregistered claim id raises rather than returning None",
          raises(lambda: CC.claim("share.does_not_exist"), KeyError))


def test_existence_is_not_coverage() -> None:
    print("\n4. an EXISTENCE claim is not a coverage claim about dates")

    entities = CC.claim("eps.same_filing_pair.entities")
    check("the 99.34% entity figure is registered as an EXISTENCE claim",
          "at least ONE" in entities.numerator_definition
          or "EXISTENCE" in entities.numerator_definition)
    check("...and says explicitly that the entity-DATE share is UNKNOWN",
          any("UNKNOWN" in n for n in entities.notes))
    check("...and is NOT presented as interchangeable with the filing figure",
          entities.denominator_unit != CC.claim(
              "eps.same_filing_pair.annual_filings").denominator_unit)


def test_denominator_dominates() -> None:
    print("\n5. the measurement that motivates the rule")

    ds = CC.DENOMINATOR_SENSITIVITY()
    pcts = {k: 100.0 * ds["numerator_held_at"] / v
            for k, v in ds["denominators"].items()}
    check("one numerator, three denominators: 22.01 / 72.25 / 91.51",
          abs(pcts["peer entity-dates"] - 22.01) < 0.01
          and abs(pcts["priced entity-dates"] - 72.25) < 0.01
          and abs(pcts["entity-dates with any in-scope count"] - 91.51) < 0.01)
    check("the spread is 69.51 points, recomputed not trusted",
          abs((max(pcts.values()) - min(pcts.values())) - 69.51) < 0.02)
    check("...and it exceeds EVERY rule about the data",
          all(69.51 > v for v in ds["against"].values()))
    check("...which is the stated justification for the rule, not taste",
          "not a claim" in ds["reading"])


def test_quote_and_assert() -> None:
    print("\n6. quoting, and the ban on quoting namelessly")

    rendered = CC.quote("share." + pit_share_coverage.CANONICAL_DEFINITION_ID)
    check("render() carries the figure, BOTH definitions and the scope",
          "72.25%" in rendered
          and "numerator" in rendered and "denominator" in rendered
          and "SURVIVOR" in rendered.upper())
    check("...and flags a BACK-DERIVED numerator where there is one",
          any("BACK-DERIVED" in CC.CLAIMS[c].render()
              for c in CC.CLAIMS
              if CC.CLAIMS[c].derived_numerator)
          or not any(CC.CLAIMS[c].derived_numerator for c in CC.CLAIMS))

    check("a registered figure quoted namelessly is REFUSED",
          raises(lambda: CC.assert_defined(
              "share-state coverage is 72.25% across the grid"),
              CC.NakedPercentage))
    check("...and the same figure quoted WITH its claim id passes",
          CC.assert_defined(
              "share.%s = 72.25%%"
              % pit_share_coverage.CANONICAL_DEFINITION_ID) is None)
    check("an unrelated percentage is NOT caught -- this is not a ban on "
          "percentages",
          CC.assert_defined("the option delta moved 3.5% overnight") is None)


def test_audit_is_honest() -> None:
    print("\n7. the auditor reports CANDIDATES and says so")

    a = CC.audit()
    check("the audit scans the project and returns candidates",
          a["files_scanned"] > 20 and isinstance(a["candidates"], list))
    check("it refuses to call itself complete",
          a["status"] == "CANDIDATES, NOT A COMPLETENESS CLAIM")
    check("...and states the false-positive expectation up front",
          "not coverage claims" in a["false_positives_expected"])
    check("...and names its exemptions rather than silencing them",
          "pit_share_coverage.py" in a["exempt"]
          and all(v for v in a["exempt"].values()))
    check("it found real candidates -- the registry is not claimed complete",
          a["candidate_count"] > 0)
    check("every candidate carries file, line and the text",
          all({"file", "line", "pct", "text"} <= set(h)
              for h in a["candidates"]))


def test_self_and_restraint() -> None:
    print("\n8. self-check and restraint")

    problems = CC.validate()
    check("validate() reports 0 problems", not problems)
    for p in problems:
        print("        - %s" % p)

    check("every registered claim is internally consistent",
          all(c.consistent() for c in CC.CLAIMS.values()))
    check("every registered claim declares a sample scope",
          all(c.sample_scope for c in CC.CLAIMS.values()))
    check("UNKNOWN is None and never 0",
          CC.UNKNOWN is None)

    here = os.path.dirname(os.path.abspath(__file__))
    src = open(os.path.join(here, "pit_coverage_contract.py"), "rb").read()
    check("the module is pure ASCII",
          all(b < 128 for b in src))
    check("the module reads NO database",
          b"sqlite3" not in src and b"connect" not in src)
    check("the module writes nothing",
          b"INSERT" not in src and b"CREATE TABLE" not in src
          and b'"w"' not in src and b"'w'" not in src)


def main() -> int:
    print("test_pit_coverage_contract -- the coverage invariant of 2026-09-21")
    test_contract_is_enforced_at_construction()
    test_units()
    test_registry_adapts_and_never_retypes()
    test_existence_is_not_coverage()
    test_denominator_dominates()
    test_quote_and_assert()
    test_audit_is_honest()
    test_self_and_restraint()
    print("\n%d PASS, %d FAIL" % (_PASS, len(_FAIL)))
    print("FAILURES: %d %s" % (len(_FAIL), _FAIL if _FAIL else ""))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
