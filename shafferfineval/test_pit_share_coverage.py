"""Tests for pit_share_coverage. Plain script: check(), main(), PASS/FAIL, exit.

Three halves, by cost:

  SYNTHETIC   the definitions, the arithmetic behind every published figure,
              the canonical block's shape and the quote guard. No store
              needed, so a regression in the RULE fails even on a machine with
              no database.
  LIVE        ~12s, read-only: the DENOMINATORS, re-counted against the real
              9.0 GiB store, plus the ladder-rung reachability sample. These
              are the numbers two of the four figures disagreed about, and
              they are cheap because a denominator is a COUNT and a numerator
              is a policy.
  --full      ~131s: `verify_selectors`, which re-measures the claim that the
              pit_coverage and pit_sharecoverage selectors return the same
              4,410 peers at 2015-06-30. That claim is what collapses two of
              the four figures into one number over two denominators, so it is
              kept runnable rather than merely recorded.

WHAT THIS TEST WILL NOT DO. It does not run the replay, freeze a baseline, fit
anything, or write one byte: the live half opens `mode=ro` and the last check
asserts the database file and its WAL are the same size afterwards as before.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pit_share_coverage as psc

DB = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                  "shafferfineval_pit.db")

_PASS = 0
_FAIL = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if cond:
        _PASS += 1
        print("PASS  %s" % name)
    else:
        _FAIL += 1
        print("FAIL  %s%s" % (name, ("  -- " + detail) if detail else ""))


def raises(fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except psc.UndefinedQuote:
        return True
    except Exception:            # the wrong exception is still a failure
        return False
    return False


# --------------------------------------------------------------------------
# SYNTHETIC: the canonical number and its definition
# --------------------------------------------------------------------------

def test_canonical() -> None:
    spec = psc.CANONICAL
    check("the constant equals its definition's counts",
          abs(psc.PIT_SHARE_STATE_COVERAGE_V2 - (spec.pct or 0)) < 0.005,
          "%s vs %s" % (psc.PIT_SHARE_STATE_COVERAGE_V2, spec.pct))
    check("the canonical is importable as a number, not prose",
          isinstance(psc.PIT_SHARE_STATE_COVERAGE_V2, float))
    check("272,587 / 377,304 is the canonical ratio",
          spec.numerator == 272587 and spec.denominator == 377304)
    check("the canonical is entity-DATES, not entities",
          spec.denominator_unit == "entity_dates")
    check("the canonical carries the survivor label",
          spec.sample_scope == psc.SAMPLE_SCOPE == "SURVIVOR_ONLY_DIAGNOSTIC")
    check("the canonical is registered under its own figure",
          psc.LEGACY_FIGURES["72.25"] == psc.CANONICAL_DEFINITION_ID)
    check("the canonical is not back-derived from a percentage",
          spec.derived_numerator is False)


def test_block_shape() -> None:
    block = psc.canonical_block()
    lines = block.splitlines()
    check("line 1 is the constant and the number",
          lines[0] == "PIT_SHARE_STATE_COVERAGE_V2 = 72.25%", lines[0])
    for label in ("Universe:", "As-of grid:", "Allowed concepts:",
                  "Staleness/state rule:", "Exclusions:"):
        starts = [ln for ln in lines if ln.startswith(label)]
        check("the block has exactly one %r line" % label, len(starts) == 1,
              repr(starts))
    values = [ln for ln in lines[1:] if ln and not ln.startswith(" ")]
    check("every block line is a labelled field", len(values) == 5, repr(values))
    check("continuation lines align under the value column",
          all(ln.startswith(" " * 22) for ln in lines[1:] if ln.startswith(" ")))
    check("the block names the survivor scope in its exclusions",
          "SURVIVOR_ONLY_DIAGNOSTIC" in block)
    check("the block refuses the period-average tag by name",
          "WeightedAverageNumberOfDilutedSharesOutstanding" in block
          and "NOT admitted" in block)
    check("the block names the v1 comparator rather than hiding it",
          "53.70%" in block and psc.DEF_DEFENSIBLE_PRICED_V1 in block)
    check("the block is generated from the definition, not typed",
          psc.CANONICAL.as_of_grid in block
          and "{:,}".format(psc.CANONICAL.numerator) in block
          and "{:,}".format(psc.CANONICAL.denominator) in block)


def test_exclusions_account_for_the_gap() -> None:
    """Every entity-date not in the numerator is named in the exclusions."""
    refusals = {"only a period average": 48956,
                "beyond the 24-month ceiling": 18328,
                "never filed a share count": 17279,
                "not yet filed": 13201,
                "guard refusals": 6248,
                "zero count": 705}
    gap = psc.PRICED_ENTITY_DATES - (psc.CANONICAL.numerator or 0)
    check("the exclusion counts sum to the uncovered entity-dates",
          sum(refusals.values()) == gap == 104717,
          "%d vs %d" % (sum(refusals.values()), gap))
    for value in refusals.values():
        check("exclusion %d appears in the published block" % value,
              "%d" % value in psc.EXCLUSIONS_LINE.replace(",", "")
              or "{:,}".format(value) in psc.EXCLUSIONS_LINE)
    check("guard refusals plus zero counts are the measured attrition",
          refusals["guard refusals"] + refusals["zero count"]
          == psc.GUARD_ATTRITION_V2 == 6953)


# --------------------------------------------------------------------------
# SYNTHETIC: the four definitions and the walk between them
# --------------------------------------------------------------------------

def test_definitions() -> None:
    check("four legacy definitions plus the canonical", len(psc.DEFINITIONS) == 5)
    for spec in psc.DEFINITIONS.values():
        check("%s: published figure follows from its counts" % spec.definition_id,
              spec.consistent(),
              "published %s, counts give %s" % (spec.published, spec.pct))
        check("%s: the period average is never an effective concept"
              % spec.definition_id,
              psc.CONCEPT_WAVG not in spec.effective_concepts)
        check("%s: names the code that produced it" % spec.definition_id,
              "." in spec.produced_by and len(spec.numerator_rule) > 40)
        check("%s: declares a denominator unit" % spec.definition_id,
              spec.denominator_unit in ("entities", "entity_dates", "listing_rows"))
        check("%s: says which member of its series the counts belong to"
              % spec.definition_id, spec.anchor_pct is not None)
    banded = [s2 for s2 in psc.DEFINITIONS.values()
              if "/" in s2.published or "-" in s2.published]
    check("the three figures that circulate as a series say so",
          len(banded) == 3, repr([s2.definition_id for s2 in banded]))
    ids = {s.definition_id for s in psc.DEFINITIONS.values()}
    check("every definition is keyed by its own id", ids == set(psc.DEFINITIONS))


def test_the_two_that_differ_only_by_denominator() -> None:
    a = psc.DEFINITIONS[psc.DEF_LADDER_BASE_V1]
    b = psc.DEFINITIONS[psc.DEF_USABLE_PEERS_V1]
    for field in ("effective_concepts", "staleness_rule", "guard"):
        check("A and B agree on %s" % field,
              getattr(a, field) == getattr(b, field))
    check("A and B differ on the denominator",
          a.denominator != b.denominator and a.denominator == 7073
          and b.denominator == 7950)
    check("A's numerator is a subset of B's (base restriction)",
          (a.numerator or 0) < (b.numerator or 0)
          and (b.numerator or 0) - (a.numerator or 0) == 88)
    check("neither requires a listing", "none" in a.listing_gate
          and "none" in b.listing_gate)
    check("the higher figure is the one with fewer requirements",
          (a.pct or 0) > (b.pct or 0))


def test_reconciliation() -> None:
    walk = psc.reconciliation()
    steps = walk["steps"]
    check("the walk has eight steps", len(steps) == 8, str(len(steps)))
    for step in steps:
        if step["definition_id"]:
            spec = psc.DEFINITIONS[step["definition_id"]]
            check("step %r reproduces %s" % (step["step"], spec.definition_id),
                  abs((step["pct"] or 0) - (spec.pct or 0)) < 0.005,
                  "%s vs %s" % (step["pct"], spec.pct))
    unlabelled = [s for s in steps if not s["definition_id"]]
    check("intermediate steps are NOT labelled as published figures",
          len(unlabelled) == 3
          and all(s["related_definition_id"] for s in unlabelled))
    symbol, guard = steps[2], steps[3]
    check("the symbol gate costs 2,356 and the guard costs 39",
          steps[0]["numerator"] - symbol["numerator"] == 2356
          and symbol["numerator"] - guard["numerator"] == 39,
          "%r %r" % (symbol["numerator"], guard["numerator"]))
    check("the guard is a small minority of the fall to 25.35%",
          (symbol["numerator"] - guard["numerator"])
          < 0.02 * (steps[0]["numerator"] - guard["numerator"]))
    check("the walk starts at the shared numerator",
          steps[0]["numerator"] == 4410 and steps[0]["denominator"] == 7950)
    check("the walk ends on the canonical number",
          steps[-1]["definition_id"] == psc.CANONICAL_DEFINITION_ID
          and abs((steps[-1]["pct"] or 0) - psc.PIT_SHARE_STATE_COVERAGE_V2) < 0.005)
    check("every step says what moved it",
          all(s["moved_by"] in ("start", "denominator", "eligibility",
                                "denominator+eligibility", "rule", "grid",
                                "rule+grid") for s in steps))
    check("the one back-derived numerator is declared as such",
          sum(1 for s in steps if "back-derived" in s["measured"]) == 1)
    check("only definition A still rests on a back-derived count",
          [d.definition_id for d in psc.DEFINITIONS.values()
           if d.derived_numerator] == [psc.DEF_LADDER_BASE_V1])
    check("the per-date measured series are carried, not just percentages",
          psc.COUNT_RESOLVABLE["2015-06-30"] == 4410
          and psc.COUNT_AND_SYMBOL["2015-06-30"] == 2054
          and psc.DEFENSIBLE_PEERS["2024-06-28"] == 3023)
    for day in psc.CENSUS_DATES:
        check("%s: count >= count+symbol >= defensible" % day,
              psc.COUNT_RESOLVABLE[day] >= psc.COUNT_AND_SYMBOL[day]
              >= psc.DEFENSIBLE_PEERS[day])
    check("only 2015-06-30 has a measured bridge",
          raises_value(psc.reconciliation, "2019-06-28"))


def raises_value(fn, *args) -> bool:
    try:
        fn(*args)
    except ValueError:
        return True
    except Exception:
        return False
    return False


# --------------------------------------------------------------------------
# SYNTHETIC: the rule -- a legacy figure may not be quoted namelessly
# --------------------------------------------------------------------------

def test_lookup_and_quote() -> None:
    check("'52%' resolves to the peer-universe usable figure",
          psc.lookup("52").definition_id == psc.DEF_USABLE_PEERS_V1)
    check("'~62%' and 62.0 and '62.0 %' are one key",
          psc.lookup("~62%").definition_id == psc.lookup(62.0).definition_id
          == psc.lookup("62.0 %").definition_id == psc.DEF_LADDER_BASE_V1)
    check("'25.35%' resolves to the defensible/symbol figure",
          psc.lookup("25.35").definition_id == psc.DEF_DEFENSIBLE_PEERS_V1)
    check("an unregistered percentage is refused, not guessed",
          raises(psc.lookup, "99.9"))
    check("a non-percentage is refused", raises(psc.lookup, "banana"))
    check("a quote carries its definition id", psc.DEF_USABLE_PEERS_V1
          in psc.quote("52"))
    check("a quote carries the published spelling, not a fake precision",
          "~52-55%" in psc.quote("52"))
    check("quoting a figure under the WRONG definition is refused",
          raises(psc.quote, "52", psc.DEF_STATE_PRICED_V2))
    check("quoting it under the right definition is allowed",
          psc.quote("52", psc.DEF_USABLE_PEERS_V1).startswith("52%"))
    check("every registered figure maps to a real definition",
          all(v in psc.DEFINITIONS for v in psc.LEGACY_FIGURES.values()))
    check("every definition has at least one registered spelling",
          set(psc.LEGACY_FIGURES.values()) == set(psc.DEFINITIONS))


def test_assert_quotable() -> None:
    check("a bare legacy figure is refused",
          raises(psc.assert_quotable, "share coverage is 52% of the universe"))
    check("the same figure with its definition id passes",
          psc.assert_quotable(
              "share coverage is 52% [SHARE_COV_B_USABLE_PEERS_V1]") is None)
    check("the canonical number is refused bare",
          raises(psc.assert_quotable, "coverage is 72.25%"))
    check("the canonical number passes when the CONSTANT is named",
          psc.assert_quotable(
              "PIT_SHARE_STATE_COVERAGE_V2 = 72.25%") is None)
    check("an unregistered percentage is not policed",
          psc.assert_quotable("the ICC is 7% and the median is 89.5%") is None)
    check("several offenders are reported together",
          raises(psc.assert_quotable, "62% then 25.35% then 72.25%"))
    check("the error names the definition the reader needs",
          _message(psc.assert_quotable, "coverage is 41.35%").find(
              psc.DEF_DEFENSIBLE_PEERS_V1) >= 0)
    check("this module's own published block passes its own rule",
          psc.assert_quotable(psc.canonical_block()) is None)
    # The rule is only real if the module that writes it obeys it. Both of
    # these caught real offences when first run.
    check("the module docstring obeys its own rule",
          psc.assert_quotable(psc.__doc__) is None,
          _message(psc.assert_quotable, psc.__doc__))
    check("the printed report obeys its own rule",
          psc.assert_quotable(psc.report()) is None,
          _message(psc.assert_quotable, psc.report()))
    check("the reconciliation prose obeys its own rule",
          psc.assert_quotable(repr(psc.reconciliation())) is None,
          _message(psc.assert_quotable, repr(psc.reconciliation())))
    check("the sensitivity prose obeys its own rule",
          psc.assert_quotable(repr(psc.sensitivity())) is None,
          _message(psc.assert_quotable, repr(psc.sensitivity())))


def _message(fn, *args) -> str:
    try:
        fn(*args)
    except psc.UndefinedQuote as exc:
        return str(exc)
    return ""


def test_audit() -> None:
    """The rule as a lint over the project, with its backlog and its limits."""
    found = psc.audit_sources()
    check("the audit scanned the project", found["scanned_files"] > 40,
          str(found["scanned_files"]))
    check("this module is not itself an offender",
          "pit_share_coverage.py" not in found["offenders"],
          repr(found["offenders"].get("pit_share_coverage.py")))
    for name in ("pit_coverage.py", "pit_derive.py"):
        check("the audit finds the unnamed defensible series in %s" % name,
              any(h["definition_id"] == psc.DEF_DEFENSIBLE_PEERS_V1
                  for h in found["offenders"].get(name, [])),
              repr(found["offenders"].get(name)))
    check("every hit names the definition the reader needs",
          all(h["definition_id"] in psc.DEFINITIONS
              for hits in found["offenders"].values() for h in hits))
    check("every hit carries a file line and the line's text",
          all(h["line"] > 0 and h["text"] for hits in found["offenders"].values()
              for h in hits))
    check("the audit declares itself a report, not a gate",
          "not a gate" in found["status"])
    check("the audit admits it cannot know what a number counts",
          "STRING matcher" in found["false_positives_expected"])
    check("the exemption list starts empty", psc.AUDIT_EXEMPT == ())


# --------------------------------------------------------------------------
# SYNTHETIC: sensitivity
# --------------------------------------------------------------------------

def test_ceiling_sensitivity() -> None:
    table = psc.ceiling_sensitivity()
    rows = table["rows"]
    admitted = [r["admitted_entity_dates"] for r in rows]
    check("the ceiling curve is monotone non-decreasing",
          all(x <= y for x, y in zip(admitted, admitted[1:])), repr(admitted))
    check("the 4-month end is the measured v1 figure, not an estimate",
          rows[0]["basis"] == "measured"
          and rows[0]["defensible_entity_dates"] == psc.V1_DEFENSIBLE_ENTITY_DATES
          and abs(rows[0]["defensible_pct"] - 53.70) < 0.01)
    check("every other row declares itself an estimate",
          all("estimated" in r["basis"] for r in rows[1:]))
    check("4 -> 12 months buys far more than 24 -> 36",
          (266188 - 206817) > 10 * (277994 - 274439))
    check("the unbounded case is reported and is not a proposal",
          rows[-1]["ceiling"] == "unbounded" and "2011" in table["if_removed"])
    gap = table["method_gap"]
    check("the curve and the policy are reconciled, not conflated",
          gap["gap_entity_dates"] == 5101
          and gap["v2_resolved_at_24m"] == psc.V2_RESOLVED_ENTITY_DATES)
    check("v2 resolved = defensible + guard attrition",
          psc.V2_RESOLVED_ENTITY_DATES
          == (psc.CANONICAL.numerator or 0) + psc.GUARD_ATTRITION_V2)
    check("the chosen row warns that it is not the headline",
          "MEASURED under this ceiling" in rows[5]["basis"])


def test_denominator_sensitivity() -> None:
    table = psc.denominator_sensitivity()
    rows = {r["denominator"]: r for r in table["rows"]}
    check("the numerator is held fixed at the canonical one",
          table["numerator_held_fixed"] == psc.CANONICAL.numerator)
    check("the canonical denominator reproduces the canonical number",
          abs(rows["priced entity-dates (CANONICAL)"]["pct"]
              - psc.PIT_SHARE_STATE_COVERAGE_V2) < 0.005)
    check("the peer-entity-date denominator reads 22.01%",
          abs(rows["peer entity-dates"]["pct"] - 22.01) < 0.01)
    check("the in-scope denominator reads 91.51%",
          abs(rows["entity-dates with any in-scope count"]["pct"] - 91.51) < 0.01)
    for name in ("base entity-dates (pooled)", "scored-universe ROWS"):
        check("%s is UNKNOWN, written as None and never as 0" % name,
              rows[name]["n"] is None and rows[name]["pct"] is None)
    check("the denominator lever outweighs every other lever combined",
          table["spread_points"] > (18.54 + 1.84 + 24.14),
          str(table["spread_points"]))
    check("the row-vs-entity trap is named in the table",
          "counted twice" in rows["scored-universe ROWS"]["why"])


def test_guard_sensitivity() -> None:
    table = psc.guard_sensitivity()
    for row in table["rows"]:
        check("%s: the guard only ever removes" % row["policy"],
              row["guard_on_entity_dates"] < row["guard_off_entity_dates"]
              and row["cost_points"] > 0)
    v1, v2 = table["rows"]
    check("guard off under v2 is 74.09%", abs(v2["guard_off_pct"] - 74.09) < 0.01)
    check("guard on under v2 is the canonical number",
          abs(v2["guard_on_pct"] - psc.PIT_SHARE_STATE_COVERAGE_V2) < 0.005)
    check("the guard costs more under v2, and the reason is stated",
          v2["cost_points"] > v1["cost_points"]
          and "72,723 more counts" in table["why_it_costs_more_under_v2"])


def test_unknowns_are_none() -> None:
    sens = psc.sensitivity()
    check("survivorship is UNKNOWN and directional, not a number",
          "UNKNOWN" in sens["unquantified"]["survivorship"]
          and "LOWER" in sens["unquantified"]["survivorship"])
    check("the per-class ingest loss is UNKNOWN, not zero",
          sens["unquantified"]["per_class_ingest"].startswith("UNKNOWN"))
    check("no pooled base denominator is invented",
          psc.MEASURED["denominators"].get("base_entity_dates") is None)


# --------------------------------------------------------------------------
# LIVE: the denominators, re-counted. Read-only.
# --------------------------------------------------------------------------

def test_live(full: bool = False) -> None:
    if not os.path.exists(DB):
        print("SKIP  live checks: %s not present" % DB)
        return
    before = (os.path.getsize(DB),
              os.path.getsize(DB + "-wal") if os.path.exists(DB + "-wal") else 0)
    res = psc.resources(DB)
    check("the WAL is READ, not assumed", isinstance(res["wal_bytes"], int))
    check("free space is above the floor this work needs",
          res["volume_free_gib"] > 1.0, repr(res))
    conn = psc.connect_readonly(DB)
    try:
        check("the handle is read-only",
              raises_operational(conn,
                                 "CREATE TABLE _t_should_not_exist (x INTEGER)"))
        live = psc.verify_denominators(conn)
        check("the grid is 165 month-end dates", live["grid_dates"] == 165,
              str(live["grid_dates"]))
        for day, rec in live["per_date"].items():
            check("%s: peer universe reproduces %d"
                  % (day, rec["published_peer"]),
                  rec["peer_universe"] == rec["published_peer"], repr(rec))
            check("%s: peer-set membership equals the peer universe" % day,
                  rec["peer_set_membership"] == rec["peer_universe"], repr(rec))
            check("%s: scored ROWS reproduce %d"
                  % (day, rec["published_scored_rows"]),
                  rec["scored_rows"] == rec["published_scored_rows"], repr(rec))
            check("%s: scored ENTITIES reproduce %d"
                  % (day, rec["published_priced_entities"]),
                  rec["scored_entities"] == rec["published_priced_entities"],
                  repr(rec))
            check("%s: rows exceed entities -- a listing is not an issuer" % day,
                  rec["rows_minus_entities"] > 0, repr(rec))
        rung = psc.verify_rung_reachability(conn, n_entities=400 if full else 120)
        check("the weighted-average rung is not materially reachable at qtrs=0",
              rung["materially_reachable"] is False,
              repr({k: rung[k] for k in ("weighted_average_rows",
                                         "weighted_average_qtrs0",
                                         "weighted_average_qtrs0_pct")}))
        check("the balance-sheet rung IS reachable at qtrs=0",
              rung["rows_by_qtrs"]["CommonStockSharesOutstanding"].get("0", 0) > 0)
        if full:
            sel = psc.verify_selectors(conn, "2015-06-30")
            recorded = psc.MEASURED["selector_identity_2015_06_30"]
            check("the two v1 selectors return the SAME peers",
                  sel["identical"], repr(sel))
            check("and that set is the recorded 4,410",
                  sel["in_both"] == recorded["in_both"] == 4410, repr(sel))
            check("the weighted-average rung rescues nobody",
                  sel["weighted_average_rung_rescues"] == 0, repr(sel))
            check("the peer denominator behind it is 7,950",
                  sel["n_peers"] == 7950, repr(sel))
    finally:
        conn.close()
    after = (os.path.getsize(DB),
             os.path.getsize(DB + "-wal") if os.path.exists(DB + "-wal") else 0)
    check("nothing was written: db and WAL are unchanged",
          before == after, "%r -> %r" % (before, after))


def raises_operational(conn, sql: str) -> bool:
    import sqlite3
    try:
        conn.execute(sql)
    except sqlite3.OperationalError:
        return True
    except Exception:
        return False
    return False


def main() -> int:
    full = "--full" in sys.argv
    print("=" * 70)
    print("pit_share_coverage%s" % ("  [--full]" if full else ""))
    print("=" * 70)
    test_canonical()
    test_block_shape()
    test_exclusions_account_for_the_gap()
    test_definitions()
    test_the_two_that_differ_only_by_denominator()
    test_reconciliation()
    test_lookup_and_quote()
    test_assert_quotable()
    test_audit()
    test_ceiling_sensitivity()
    test_denominator_sensitivity()
    test_guard_sensitivity()
    test_unknowns_are_none()
    test_live(full=full)
    print("-" * 70)
    print("%d checks: %d passed, %d failed" % (_PASS + _FAIL, _PASS, _FAIL))
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
