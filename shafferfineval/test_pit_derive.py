"""Offline tests for the derivation graph.

Pure stdlib, no network, NO DATABASE:   python test_pit_derive.py

Two kinds of check live here. The first kind guards the arithmetic: a joint
availability that is wrong in the optimistic direction would let the project
go on believing a feature is cheaper than it is, which is the same class of
error as a backtest leak. The second kind guards the CLASSIFICATION, and it is
the reason this module exists: a derived metric that no provider publishes
must never, under any circumstance, come back as GENUINELY_UNAVAILABLE.

The last test is the one about restraint. `pit_derive` describes the
computation and does not perform it, so it must open no connection, run no
SQL, and leave `pit_feature` and `pit_score` exactly as empty as it found
them. That is asserted here rather than promised in a docstring.
"""
from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_derive as D

fails: list[str] = []


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


def _raises(thunk, exc) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


def _close(a: float, b: float, tol: float = 1e-9) -> bool:
    """Relative where the numbers are large, absolute where they are small.

    Dollars run to 1e9 here, so a bare absolute epsilon would fail on the last
    bit of a nine-figure subtraction and pass nothing useful."""
    return math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=tol)


class _FakeConn:
    """A connection that records instead of connecting. Nothing is a database."""

    def __init__(self) -> None:
        self.scripts: list[str] = []
        self.commits = 0

    def executescript(self, sql: str) -> None:
        self.scripts.append(sql)

    def commit(self) -> None:
        self.commits += 1


def main() -> int:
    print("== 1. the registry is a well-formed DAG ==")
    check("the graph is acyclic", D.is_acyclic())
    check("validate_graph finds nothing to complain about",
          D.validate_graph() == [], D.validate_graph())
    order = D.topological_order()
    check("every node appears exactly once in topological order",
          len(order) == len(D.NODES) and set(order) == set(D.NODES))
    positions = {key: i for i, key in enumerate(order)}
    check("every input precedes its consumer in that order",
          all(positions[edge.key] < positions[key]
              for key, item in D.NODES.items() for edge in item.inputs))
    # A cycle must FAIL LOUDLY rather than hang: every walk in this module is
    # a graph traversal, and an infinite one looks like a slow test.
    injected = D.Node(key="_cycle_a", kind=D.KIND_DERIVED, scope=D.SCOPE_COMPANY,
                      formula="_cycle_a = _cycle_b", question="Does a cycle raise?",
                      inputs=(D.Input("_cycle_b"),))
    partner = D.Node(key="_cycle_b", kind=D.KIND_DERIVED, scope=D.SCOPE_COMPANY,
                     formula="_cycle_b = _cycle_a", question="Does a cycle raise?",
                     inputs=(D.Input("_cycle_a"),))
    D.NODES["_cycle_a"], D.NODES["_cycle_b"] = injected, partner
    try:
        check("an injected cycle is detected, not looped over",
              not D.is_acyclic() and _raises(D.topological_order, ValueError))
        check("validate_graph reports the cycle in words",
              any("acyclic" in problem for problem in D.validate_graph()))
    finally:
        del D.NODES["_cycle_a"], D.NODES["_cycle_b"]
    check("the registry is restored after the injection",
          D.is_acyclic() and D.validate_graph() == [])

    print()
    print("== 2. the three buckets are structurally distinct ==")
    check("every derived node's inputs are registered nodes",
          all(edge.key in D.NODES
              for key in D.derived() for edge in D.node(key).inputs))
    check("every derived node HAS inputs (a number with no provenance is not derived)",
          all(D.node(key).inputs for key in D.derived()))
    check("a primitive has no inputs",
          all(not D.node(key).inputs for key in D.primitives()))
    check("a primitive names the ladder or table that supplies it",
          all(D.node(key).source for key in D.primitives()))
    check("every genuinely-unavailable node is a primitive with no inputs",
          all(not D.node(key).inputs
              for key in D.nodes_of_kind(D.KIND_UNAVAILABLE)))
    check("every node's formula states the arithmetic",
          all("=" in D.node(key).formula for key in D.derived()))
    check("the owner's whole enumeration is registered",
          all(key in D.NODES for key in (
              "revenue", "operating_income", "depreciation_amortisation",
              "net_income", "total_debt", "cash", "total_assets", "equity",
              "operating_cash_flow", "capex", "interest_expense",
              "shares_outstanding", "price_raw", "price_adjusted", "sic",
              "cpi", "treasury_yield",
              "ebitda", "ebitda_margin", "ebitda_growth", "ebitda_acceleration",
              "revenue_growth", "real_revenue_growth", "free_cash_flow",
              "market_cap", "enterprise_value", "pe_ratio", "ev_ebitda",
              "fcf_yield", "net_debt_ebitda", "interest_coverage", "roa",
              "fcf_conversion", "sector_ebitda_p50", "sector_ebitda_p75",
              "cohort_50_75_membership", "cohort_mean_ebitda",
              "cohort_mean_margin", "ebitda_benchmark_gap",
              "ebitda_excess_level", "ebitda_margin_rank", "ebitda_scale",
              "company_score", "sector_score", "shaffer_score")))

    print()
    print("== 3. cohort-level is explicit, not implied ==")
    cohort = set(D.cohort_nodes())
    for key in ("sector_ebitda_p50", "sector_ebitda_p75",
                "cohort_50_75_membership", "cohort_mean_ebitda",
                "cohort_mean_margin", "cohort_mean_ev_ebitda", "sector_score"):
        check(f"{key} is marked COHORT-level", key in cohort)
    for key in ("ebitda", "ebitda_margin", "company_score", "shaffer_score"):
        check(f"{key} is company-level", D.node(key).scope == D.SCOPE_COMPANY)
    check("a cohort statistic is stored once per cohort, not once per company",
          D.storage_recommendation()["options"]["split_recommended"]
          ["cohort_rows_per_run"] == 39_038)

    print()
    print("== 4. coverage propagates on a hand-computed example ==")
    # free_cash_flow = operating_cash_flow - capex. By hand:
    #   product        0.90 x 0.50 = 0.45
    #   Frechet lower  max(0, 1.40 - 1) = 0.40
    #   Frechet upper  min(0.90, 0.50) = 0.50
    hand = {"operating_cash_flow": 0.90, "capex": 0.50}
    fcf = D.coverage("free_cash_flow", hand)
    check("two-leaf product is the product", _close(fcf["joint_independent"], 0.45))
    check("two-leaf Frechet lower is max(0, sum - (n-1))",
          _close(fcf["frechet_lower"], 0.40))
    check("two-leaf Frechet upper is the scarcest marginal",
          _close(fcf["frechet_upper"], 0.50))
    check("the bracket contains the product",
          fcf["frechet_lower"] <= fcf["joint_independent"] <= fcf["frechet_upper"])

    # enterprise_value = market_cap + total_debt - cash, four primitives.
    #   0.50 x 0.40 x 0.20 x 0.90 = 0.036 ; lower max(0, 2.00 - 3) = 0
    deep = {"price_adjusted": 0.50, "shares_outstanding": 0.40,
            "total_debt": 0.20, "cash": 0.90}
    ev = D.coverage("enterprise_value", deep)
    check("a four-leaf joint is the four-way product",
          _close(ev["joint_independent"], 0.036))
    check("the four-leaf Frechet lower floors at zero",
          _close(ev["frechet_lower"], 0.0))
    check("the Frechet ceiling survives two levels of nesting",
          _close(ev["frechet_upper"], 0.20))
    check("the leaves are the PRIMITIVES, not the intermediate market_cap",
          sorted(r["leaf"] for r in ev["leaves"]) ==
          ["cash", "price_adjusted", "shares_outstanding", "total_debt"])

    # A lagged leaf is its own leaf: growth needs the fact at TWO dates.
    lagged = D.coverage("revenue_growth", {"revenue": 0.80})
    check("a lag creates a second leaf", lagged["n_leaves"] == 2)
    check("the lagged leaf is named as such",
          "revenue@t-1y" in [r["leaf"] for r in lagged["leaves"]])
    check("growth is the square of the level when the lag is assumed persistent",
          _close(lagged["joint_independent"], 0.64))
    check("the lag assumption is declared, never silent",
          any("ASSUMPTION" in note for note in lagged["notes"]))
    accel = D.coverage("ebitda_acceleration", {"operating_income": 1.0,
                                               "depreciation_amortisation": 0.50})
    check("acceleration is three EBITDA observations deep, so six leaves",
          accel["n_leaves"] == 6, accel["n_leaves"])
    check("the shared t-1 observation is charged ONCE, not twice",
          _close(accel["joint_independent"], 0.5 ** 3),
          (accel["joint_independent"], "0.0625 would mean double counting"))

    print()
    print("== 5. the binding leaf is the scarcest leaf, named ==")
    scarce = {"price_adjusted": 0.95, "shares_outstanding": 0.93,
              "total_debt": 0.04, "cash": 0.99}
    report = D.coverage("enterprise_value", scarce)
    check("one far-scarcer primitive is identified as the binding leaf",
          report["binding_leaf"] == "total_debt", report["binding_leaf"])
    check("the runner-up is the next-scarcest, not an arbitrary sibling",
          report["runner_up_leaf"] == "shares_outstanding", report["runner_up_leaf"])
    check("the Frechet ceiling equals the binding leaf's availability",
          _close(report["frechet_upper"], report["binding_availability"]))
    check("repairing the binding leaf reports the product of the rest",
          _close(report["joint_if_binding_repaired"], 0.95 * 0.93 * 0.99))
    flipped = D.coverage("enterprise_value",
                         {"price_adjusted": 0.04, "shares_outstanding": 0.93,
                          "total_debt": 0.95, "cash": 0.99})
    check("move the scarcity and the binding leaf moves with it",
          flipped["binding_leaf"] == "price_adjusted")

    print()
    print("== 6. the estimates bracket the MEASURED EBITDA joint ==")
    for day in D.MEASURED_DATES:
        marginals = D.primitive_availability(day)
        lift = D.co_occurrence_lift(day)
        ebitda = D.coverage("ebitda", marginals,
                            measured_joints={"ebitda": D.MEASURED_JOINTS["ebitda"][day]},
                            lift=lift)
        measured = D.MEASURED_JOINTS["ebitda"][day]
        check(f"{day}: the measured joint is reported beside the estimates",
              _close(ebitda["measured_joint"], measured))
        check(f"{day}: independence UNDERSTATES the measured joint",
              ebitda["joint_independent"] < measured,
              (ebitda["joint_independent"], measured))
        check(f"{day}: the Frechet bracket contains the measured joint",
              ebitda["frechet_lower"] <= measured <= ebitda["frechet_upper"],
              (ebitda["frechet_lower"], measured, ebitda["frechet_upper"]))
        check(f"{day}: the co-occurrence lift is > 1 (facts co-occur)", lift > 1.0)
        check(f"{day}: the calibrated estimate reproduces the measurement it was fitted on",
              _close(ebitda["joint_calibrated"], measured, 1e-9))
    check("2015-06-30 independence is 0.701 x 0.614 = 43.04%",
          _close(D.coverage("ebitda", D.primitive_availability("2015-06-30")
                            )["joint_independent"], 0.701 * 0.614))
    check("the measured 48.1% beats the 43.04% product by ~5.1 points",
          _close(0.481 - 0.701 * 0.614, 0.0506, 1e-4))
    check("the lift falls as tagging practice matures",
          D.co_occurrence_lift("2015-06-30") > D.co_occurrence_lift("2019-06-28")
          > D.co_occurrence_lift("2024-06-28"))

    print()
    print("== 7. a renormalising node is a UNION, never a product ==")
    blend = {"revenue": 0.6, "operating_income": 0.6,
             "depreciation_amortisation": 0.6, "total_assets": 1.0,
             "net_income": 0.6, "sic": 1.0}
    growth = D.coverage("growth_score", blend)
    check("growth_score reports its direct inputs, not flattened primitives",
          {r["leaf"] for r in growth["leaves"]} ==
          {"revenue_growth", "revenue_acceleration", "ebitda_growth", "peer_set"})
    check("a renormalising node has NO binding leaf",
          growth["binding_leaf"] is None)
    check("its availability exceeds any one component (it is a union)",
          growth["joint_independent"] >
          max(D.coverage(k, blend)["joint_independent"]
              for k in ("revenue_growth", "revenue_acceleration", "ebitda_growth")))
    check("a gate is still required on a renormalising node",
          D.node("growth_score").gate_inputs[0].key == "peer_set")
    check("the optional sector overlay is NOT charged to shaffer_score",
          "sector_overlay" not in
          [ref.key for ref in D.leaves("shaffer_score")])

    print()
    print("== 8. a derived metric is NEVER genuinely unavailable ==")
    never_published = (
        "ebitda", "ebitda_margin", "ebitda_growth", "ebitda_acceleration",
        "sector_ebitda_p50", "sector_ebitda_p75", "cohort_50_75_membership",
        "cohort_mean_ebitda", "cohort_mean_margin", "cohort_mean_ev_ebitda",
        "ebitda_benchmark_gap", "ebitda_excess_level", "ev_ebitda",
        "company_score", "sector_score", "shaffer_score",
    )
    for day in ("2005-01-03", "2015-06-30", "2026-09-21"):
        for key in never_published:
            result = D.classify(key, day)
            check(f"{key} at {day} is SHAFFER_DERIVED",
                  result["bucket"] == D.KIND_DERIVED,
                  result["bucket"] + " :: " + result["reason"][:60])
    check("the reason says so in words, not by omission",
          "No provider publishes it" in D.classify("cohort_mean_ebitda", "2019-06-28")["reason"])
    check("a primitive classifies as a primitive",
          D.classify("revenue", "2019-06-28")["bucket"] == D.KIND_SOURCE)

    print()
    print("== 9. the unavailable bucket is small, dated, and argued ==")
    check("exactly four members are registered",
          len(D.UNAVAILABLE_REGISTER) == 4, sorted(D.UNAVAILABLE_REGISTER))
    check("every register entry carries a test and a why",
          all(entry.get("test") and entry.get("why")
              for entry in D.UNAVAILABLE_REGISTER.values()))
    pre = D.classify("option_quote_pre_archive", "2019-06-28")
    post = D.classify("option_quote_pre_archive", "2026-09-21")
    check("an option quote before 2026-09-20 is genuinely unavailable",
          pre["bucket"] == D.KIND_UNAVAILABLE)
    check("the same quote on or after 2026-09-20 is an ordinary primitive",
          post["bucket"] == D.KIND_SOURCE, post["bucket"])
    check("analyst GPI is unavailable at every date",
          all(D.classify("analyst_gpi_historical", d)["bucket"] == D.KIND_UNAVAILABLE
              for d in ("2005-01-03", "2026-09-21")))
    check("a dead-company price is unavailable only when asserted for that listing",
          D.classify("price_dead_company", "2019-06-28")["bucket"] == D.KIND_SOURCE
          and D.classify("price_dead_company", "2019-06-28",
                         obtainable={"price_dead_company": False}
                         )["bucket"] == D.KIND_UNAVAILABLE)
    check("an ordinary primitive may NOT be asserted unobtainable",
          _raises(lambda: D.classify("ev_ebitda", "2019-06-28",
                                     obtainable={"total_debt": False}), ValueError))
    blocked = D.classify("ev_ebitda", "2019-06-28",
                         obtainable={"price_dead_company": False})
    check("a derived node blocked by a real primitive absence names that primitive",
          blocked["bucket"] == D.KIND_DERIVED,
          "ev_ebitda does not consume the dead-price node; nothing should block it")
    check("the admission test has four questions and names the v2 counter-example",
          len(D.unavailable_admission_test()) == 4
          and any("concept_ladder_v2" in q for q in D.unavailable_admission_test()))

    print()
    print("== 10. the trace walks every node between two named endpoints ==")
    span = D.between("ebitda", "shaffer_score")
    steps = D.trace("shaffer_score", D.FIXTURE_VALUES, start="ebitda")
    emitted = [s.key for s in steps]
    check("the span is non-empty and ordered", len(span) > 5 and emitted == list(span))
    check("both endpoints are emitted",
          emitted[0] == "ebitda" and emitted[-1] == "shaffer_score")
    for waypoint in ("ebitda_margin", "profitability_score", "company_raw_score",
                     "company_score"):
        check(f"{waypoint} is on the emitted walk", waypoint in emitted)
    check("nothing off the path sneaks in",
          "sector_pe" not in emitted and "capex" not in emitted)
    check("every step carries value, formula, provenance and availability",
          all(s.formula and s.availability and (s.source or s.inputs) for s in steps))
    full = D.trace("shaffer_score", D.FIXTURE_VALUES, also=D.TRACE_EXTRAS)
    keys = [s.key for s in full]
    for wanted in ("revenue", "ebitda", "ebitda_growth", "ebitda_margin",
                   "sector_ebitda_p50", "sector_ebitda_p75", "cohort_mean_ebitda",
                   "ebitda_benchmark_gap", "real_revenue_growth", "pe_ratio",
                   "sector_pe", "extreme_valuation_penalty", "company_score",
                   "sector_score", "shaffer_score"):
        check(f"the owner's trace contains {wanted}", wanted in keys)
    check("no node is emitted before one of its inputs",
          all(keys.index(edge.key) < keys.index(k)
              for k in keys for edge in D.node(k).inputs if edge.key in keys))
    rendered = D.render_trace(full)
    check("the rendered trace is readable text with every node in it",
          all(k in rendered for k in keys) and rendered.count("SOURCE") >= 10)
    narrowed = D.trace("shaffer_score", D.FIXTURE_VALUES, also=D.TRACE_EXTRAS,
                       only=D.OWNER_TRACE_NODES)
    check("a narrowed trace emits exactly the nodes asked for",
          {s.key for s in narrowed} == set(D.OWNER_TRACE_NODES))
    check("a narrowed trace keeps topological order, not the order asked in",
          [s.key for s in narrowed] ==
          [k for k in D.topological_order() if k in set(D.OWNER_TRACE_NODES)])
    check("the owner's fifteen named quantities are all in it",
          all(k in {s.key for s in narrowed} for k in (
              "revenue", "ebitda", "ebitda_growth", "ebitda_margin",
              "sector_ebitda_p50", "sector_ebitda_p75", "cohort_mean_ebitda",
              "ebitda_benchmark_gap", "real_revenue_growth", "pe_ratio",
              "sector_pe", "extreme_valuation_penalty", "company_score",
              "sector_score", "shaffer_score")))
    check("an unknown node in `only` is refused, not silently dropped",
          _raises(lambda: D.trace("shaffer_score", D.FIXTURE_VALUES,
                                  only=("no_such_node",)), ValueError))
    check("a path that does not exist is refused, not faked",
          _raises(lambda: D.trace("revenue", D.FIXTURE_VALUES, start="shaffer_score"),
                  ValueError))

    print()
    print("== 11. the fixture is arithmetically self-consistent ==")
    f = D.FIXTURE_VALUES
    check("ebitda = operating_income + D&A",
          _close(f["ebitda"], f["operating_income"] + f["depreciation_amortisation"]))
    check("ebitda_margin = ebitda / revenue",
          _close(f["ebitda_margin"], f["ebitda"] / f["revenue"]))
    check("ebitda_growth = (t - t-1) / abs(t-1)",
          _close(f["ebitda_growth"],
                 (f["ebitda"] - f["ebitda@t-1y"]) / abs(f["ebitda@t-1y"])))
    check("ebitda_acceleration = growth_t - growth_t-1",
          _close(f["ebitda_acceleration"],
                 f["ebitda_growth"] - f["ebitda_growth@t-1y"]))
    check("real_revenue_growth deflates rather than subtracts",
          _close(f["real_revenue_growth"],
                 (1 + f["revenue_growth"]) / (1 + f["inflation"]) - 1))
    check("market_cap = price x shares",
          _close(f["market_cap"], f["price_adjusted"] * f["shares_outstanding"]))
    check("enterprise_value = market_cap + debt - cash",
          _close(f["enterprise_value"],
                 f["market_cap"] + f["total_debt"] - f["cash"]))
    check("ev_ebitda = EV / EBITDA",
          _close(f["ev_ebitda"], f["enterprise_value"] / f["ebitda"]))
    check("pe_ratio = market_cap / net_income",
          _close(f["pe_ratio"], f["market_cap"] / f["net_income"]))
    check("the company sits above its 50-75 cohort benchmark",
          _close(f["ebitda_excess_level"], f["ebitda"] - f["cohort_mean_ebitda"], 1e-6)
          and _close(f["ebitda_benchmark_gap"],
                     f["ebitda"] / f["cohort_mean_ebitda"] - 1)
          and f["ebitda_benchmark_gap"] > 0)
    check("the 50-75 band is ordered",
          f["sector_ebitda_p50"] < f["sector_ebitda_p75"])
    check("valuation_score = 100 tanh(2 gap), the production curve",
          _close(f["valuation_score"], 100 * math.tanh(2.0 * f["valuation_gap"])))
    check("the extreme-valuation penalty is what the tanh refused",
          _close(f["extreme_valuation_penalty"],
                 f["valuation_score"] - 200 * f["valuation_gap"]))
    check("company_score = 0.75 x raw, clamped",
          _close(f["company_score"], 0.75 * f["company_raw_score"]))
    check("company_raw_score = 0.40V + 0.25G + 0.20P + 0.15D",
          _close(f["company_raw_score"],
                 0.40 * f["valuation_score"] + 0.25 * f["growth_score"]
                 + 0.20 * f["profitability_score"] + 0.15 * f["debt_score"]))
    check("sector_overlay = sector_score / 4",
          _close(f["sector_overlay"], f["sector_score"] / 4.0))
    check("shaffer_score = company_score + sector_overlay",
          _close(f["shaffer_score"], f["company_score"] + f["sector_overlay"]))

    print()
    print("== 12. the cohort gate is a count threshold, reported with its n ==")
    check("a 32-peer set at 51.3% EBITDA forms a 3-member cohort almost surely",
          D.cohort_formation_probability(0.513, 32, 3) > 0.9999)
    check("a 5-peer set at the same 51.3% forms one barely half the time",
          0.52 < D.cohort_formation_probability(0.513, 5, 3) < 0.53,
          D.cohort_formation_probability(0.513, 5, 3))
    check("the binomial is monotone in p",
          D.binomial_at_least(32, 0.2, 3) < D.binomial_at_least(32, 0.5, 3))
    check("asking for more members than exist is impossible, not rounded up",
          D.binomial_at_least(2, 0.9, 3) == 0.0)
    check("the mean peer-set size is 1,240,000 / 39,038",
          _close(D.PEER_SET_MEAN_SIZE, 1_240_000 / 39_038))

    print()
    print("== 13. the coverage report renders, for every measured date ==")
    for day in D.MEASURED_DATES:
        marginals = D.primitive_availability(day)
        text = D.coverage_report(
            marginals, label=day,
            measured_joints={"ebitda": D.MEASURED_JOINTS["ebitda"][day]},
            lift=D.co_occurrence_lift(day))
        check(f"{day}: every reported feature appears",
              all(k in text for k in D.REPORT_NODES))
        check(f"{day}: the phrase that replaces 'unavailable' is present",
              "BINDING LEAF" in text and "all primitives jointly" in text)
        check(f"{day}: unsurveyed primitives are named, not guessed",
              "NOT SURVEYED" in text and "treasury_yield" in text)
        check(f"{day}: the summary table is there",
              "BINDING LEAF SUMMARY" in text)
    check("total_debt is selected by LADDER VERSION, and the two differ",
          D.primitive_availability("2024-06-28", "concept_ladder_v1")["total_debt"]
          == 0.145
          and D.primitive_availability("2024-06-28", "concept_ladder_v2")["total_debt"]
          == 0.377)
    check("an unknown ladder version is refused",
          _raises(lambda: D.primitive_availability("2024-06-28", "v9"), ValueError))
    check("an unmeasured date is refused rather than interpolated",
          _raises(lambda: D.primitive_availability("2020-06-30"), ValueError))
    check("an unsurveyed primitive raises rather than defaulting",
          _raises(lambda: D.coverage("ebitda", {"operating_income": 0.7}), ValueError))

    print()
    print("== 14. the intermediate schema is SPECIFIED, not applied ==")
    ddl = D.PIT_INTERMEDIATE_DDL
    check("the DDL is a string constant", isinstance(ddl, str) and len(ddl) > 500)
    check("it creates the cohort-level and company-level tables",
          "CREATE TABLE IF NOT EXISTS pit_cohort_stat" in ddl
          and "CREATE TABLE IF NOT EXISTS pit_company_intermediate" in ddl)
    check("it stores every cohort statistic the owner asked for",
          all(col in ddl for col in (
              "n_sector_peers", "ebitda_p50", "ebitda_p75", "n_cohort_members",
              "cohort_mean_ebitda", "cohort_mean_margin", "company_ebitda",
              "company_ebitda_percentile", "company_ebitda_margin",
              "ebitda_benchmark_gap", "source_coverage", "confidence")))
    check("it keys the cohort table the way pit_peer_set is keyed",
          "UNIQUE (as_of_date, rung, key, model_version, peer_set_version" in ddl)
    check("it carries the store's immutability discipline",
          ddl.count("CREATE TRIGGER IF NOT EXISTS") == 2
          and ddl.count("RAISE(ABORT") == 2)
    statements = [line.strip() for line in ddl.splitlines()]
    for verb in ("DROP", "DELETE", "UPDATE", "INSERT", "ALTER", "ATTACH", "PRAGMA"):
        # "BEFORE UPDATE ON" inside a trigger is the opposite of an update: it
        # is the clause that FORBIDS one. Only a statement that STARTS with the
        # verb would change data.
        check(f"no statement starts with {verb}",
              not any(line.startswith(verb) for line in statements))
    check("every CREATE is IF NOT EXISTS, so a later run is a no-op",
          ddl.count("CREATE ") == ddl.count("IF NOT EXISTS"))
    fake = _FakeConn()
    D.ensure_schema(fake)
    check("ensure_schema runs exactly the DDL and nothing else",
          fake.scripts == [ddl] and fake.commits == 1)
    rec = D.storage_recommendation()
    check("the recommendation is the split",
          rec["recommendation"].startswith("split"))
    check("the row-count arithmetic uses the real figures",
          rec["options"]["split_recommended"]["cohort_rows_per_run"] == 39_038
          and rec["options"]["split_recommended"]["company_rows_per_run"] == 1_240_000)
    check("the denormalised alternative's repetition factor is 31.8x",
          _close(rec["options"]["denormalised_per_company_blob"]["repetition_factor"],
                 31.76, 0.01))
    check("the pit_feature route is quantified too",
          rec["options"]["cohort_stats_as_pit_feature_rows"]["rows_per_run"]
          == 11 * 1_240_000)
    check("the recommendation says out loud that it was not applied",
          "not_applied" in rec and "later" in rec["not_applied"])

    print()
    print("== 15. the module touches no database ==")
    source = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "pit_derive.py"), encoding="utf-8").read()
    check("it does not import sqlite3", "import sqlite3" not in source)
    check("sqlite3 is not reachable as a module attribute",
          not hasattr(D, "sqlite3"))
    check("it never calls connect()", "connect(" not in source)
    check("it opens no file", "open(" not in source and "Path(" not in source)
    check("it imports nothing but the standard library",
          [line for line in source.splitlines()
           if line.startswith("import ") or line.startswith("from ")]
          == ["from __future__ import annotations", "import math",
              "from dataclasses import dataclass, field",
              "from typing import Any, Callable, Iterable, Mapping, Optional, Sequence"])

    # And the behavioural half: run the whole public API with sqlite3.connect
    # replaced by a landmine. If anything in here reaches for the store, this
    # raises instead of blocking the WAL checkpointer on a 8.7 GB database.
    import sqlite3
    original = sqlite3.connect

    def _landmine(*args, **kwargs):
        raise AssertionError("pit_derive opened a database connection")

    sqlite3.connect = _landmine
    try:
        marginals = D.primitive_availability("2024-06-28")
        D.coverage_report(marginals, label="no-db",
                          measured_joints={"ebitda": 0.513},
                          lift=D.co_occurrence_lift("2024-06-28"))
        for key in D.node_keys():
            D.classify(key, "2024-06-28")
            D.leaves(key)
        D.render_trace(D.trace("shaffer_score", D.FIXTURE_VALUES,
                               also=D.TRACE_EXTRAS))
        D.storage_recommendation()
        D.validate_graph()
        opened = False
    except AssertionError:
        opened = True
    finally:
        sqlite3.connect = original
    check("the entire public API runs without opening a connection", not opened)
    check("pit_feature and pit_score stay empty: this module writes no rows",
          "INSERT INTO pit_feature" not in source
          and "INSERT INTO pit_score" not in source)

    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
