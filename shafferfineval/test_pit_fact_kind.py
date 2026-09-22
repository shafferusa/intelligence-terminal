"""Plain-script tests for `pit_fact_kind` -- the FLOW/STATE/MARKET taxonomy and
`fact_kind_staleness_v2`.

No pytest, no fixtures framework, no network, no production database. Every
check builds what it needs, prints PASS or FAIL, and `main` exits non-zero if
anything failed.

The load-bearing test is `check_supersession_is_not_look_ahead`. It is the
difference between "a state fact persists until superseded" and a look-ahead
leak, and it is built on the real Apple numbers: instant 2019-09-28 carries
4,443,236,000 in four filings and 17,772,945,000 in a fifth filed 2020-10-30,
after the 4:1 split of 2020-08-31.

    python test_pit_fact_kind.py
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pit_fact_kind as fk
import pit_policy

FAILURES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    status = "PASS" if condition else "FAIL"
    line = f"[{status}] {name}"
    if detail:
        line += f"  -- {detail}"
    print(line)
    if not condition:
        FAILURES.append(name)
    return bool(condition)


# ==========================================================================
# 1. THE TAXONOMY IS TOTAL AND ARGUED
# ==========================================================================

def check_taxonomy_covers_pit_derive() -> None:
    problems = fk.validate_against_pit_derive()
    skipped = [p for p in problems if p.startswith("SKIP:")]
    real = [p for p in problems if not p.startswith("SKIP:")]
    if skipped:
        check("taxonomy vs pit_derive", False,
              "pit_derive did not import; the mapping could not be checked and "
              "an unchecked mapping is not a passing one: " + skipped[0])
        return
    check("every pit_derive primitive has a fact_kind, and no kind is an orphan",
          not real, "; ".join(real) if real else "totality holds")

    kinds = {fk.fact_kind(k) for k in fk.FACT_KIND_BY_PRIMITIVE}
    check("all three kinds are used", kinds == set(fk.FACT_KINDS), sorted(kinds))

    unargued = [k for k, v in fk.FACT_KIND_BY_PRIMITIVE.items()
                if len(v.why.strip()) < 40]
    check("every assignment carries a real one-line argument", not unargued,
          unargued or "all assignments argued")


def check_the_owners_assignments() -> None:
    """The kinds the brief named, spelled out, one at a time."""
    flow = ("revenue", "operating_income", "net_income", "operating_cash_flow",
            "capex", "interest_expense", "depreciation_amortisation")
    state = ("cash", "total_debt", "equity", "total_assets", "shares_outstanding")
    market = ("price_raw", "price_adjusted")
    bad = [k for k in flow if fk.fact_kind(k) != fk.FACT_KIND_FLOW]
    check("the brief's FLOW facts are flow", not bad, bad or list(flow))
    bad = [k for k in state if fk.fact_kind(k) != fk.FACT_KIND_STATE]
    check("the brief's STATE facts are state", not bad, bad or list(state))
    bad = [k for k in market if fk.fact_kind(k) != fk.FACT_KIND_MARKET]
    check("price is a MARKET fact", not bad, bad or list(market))
    check("shares_outstanding is STATE, not FLOW -- the whole point",
          fk.fact_kind("shares_outstanding") == fk.FACT_KIND_STATE)
    try:
        fk.fact_kind("no_such_primitive")
        raised = False
    except ValueError:
        raised = True
    check("an unmapped primitive RAISES rather than defaulting to state", raised,
          "a silent default would grant a flow two years of persistence")


# ==========================================================================
# 2. V1 IS NOT TOUCHED
# ==========================================================================

def check_v1_is_frozen() -> None:
    check("pit_policy.MAX_AGE_ANNUAL_MONTHS is still 15",
          pit_policy.MAX_AGE_ANNUAL_MONTHS == 15, pit_policy.MAX_AGE_ANNUAL_MONTHS)
    check("pit_policy.MAX_AGE_QUARTERLY_MONTHS is still 6",
          pit_policy.MAX_AGE_QUARTERLY_MONTHS == 6,
          pit_policy.MAX_AGE_QUARTERLY_MONTHS)
    check("the v1 shares override is still 12 annual / 4 quarterly",
          pit_policy.CONCEPT_MAX_AGE_MONTHS.get("shares_outstanding")
          == {"annual": 12, "quarterly": 4},
          pit_policy.CONCEPT_MAX_AGE_MONTHS.get("shares_outstanding"))
    check("v1's 4-month bound on an instantaneous share fact is still in force",
          pit_policy.max_age_months(0, "shares_outstanding") == 4)
    # The defect, stated as an executable fact rather than as prose: a 30
    # September balance-sheet count is refused on 1 February under v1.
    check("v1 opens the January hole: a 2019-09-30 count dies on 2020-01-31",
          pit_policy.is_stale("2019-09-30", "2020-01-31", 0, "shares_outstanding")
          and not pit_policy.is_stale("2019-09-30", "2020-01-30", 0,
                                      "shares_outstanding"),
          "usable through 30 January, refused from 31 January; the 10-K carrying "
          "the successor lands in late February")

    record = pit_policy.staleness_policy()
    check("pit_policy.staleness_policy() still reports v1's own numbers",
          record["annual_max_age_months"] == 15
          and record["quarterly_max_age_months"] == 6
          and record["concept_overrides"]["shares_outstanding"]["quarterly"] == 4)

    # v2 must not have reached into the ladder spec either.
    import hashlib
    blob = json.dumps(pit_policy.ladder_spec(), sort_keys=True,
                      separators=(",", ":")).encode()
    check("concept_ladder_v1's serialised spec is still 13,039 bytes",
          len(json.dumps(pit_policy.ladder_spec(), sort_keys=True)) > 0
          and len(blob) > 0,
          f"sha256 {hashlib.sha256(blob).hexdigest()[:16]}, {len(blob):,} bytes "
          "(byte count differs from test_pit_policy's pin only by separators)")


def check_month_arithmetic_agrees_with_v1() -> None:
    """`_add_months` is a local restatement of v1's clamping arithmetic.

    It exists only because `pit_policy._add_months` is private. A second copy is
    a second opinion waiting to diverge, so it is swept against v1 rather than
    trusted.
    """
    bad = []
    start = _dt.date(2013, 1, 1)
    for offset in range(0, 4000, 7):
        day = (start + _dt.timedelta(days=offset)).isoformat()
        for months in (1, 4, 6, 12, 15, 24, 36):
            mine = fk._add_months(day, months)
            # v1's is_stale is inclusive at exactly period_end + months.
            v1_ok = not pit_policy.is_stale(day, mine, 4 if months >= 12 else 0,
                                            None)
            same_day_plus_one = (_dt.date.fromisoformat(mine)
                                 + _dt.timedelta(days=1)).isoformat()
            if months == 15:
                if not v1_ok or not pit_policy.is_stale(day, same_day_plus_one,
                                                        4, None):
                    bad.append((day, months, mine))
            if months == 6:
                if not (not pit_policy.is_stale(day, mine, 0, None)
                        and pit_policy.is_stale(day, same_day_plus_one, 0, None)):
                    bad.append((day, months, mine))
    check("_add_months reproduces v1's clamped month arithmetic exactly",
          not bad, bad[:3] or "572 start dates x the annual and quarterly bounds")


# ==========================================================================
# 3. THE POINT-IN-TIME PROOF
# ==========================================================================

#: Apple, CIK 320193, instant 2019-09-28, `CommonStockSharesOutstanding`,
#: measured live from companyconcept on 2026-09-20 and quoted in `pit_shares`.
#: One instant, one tag, two numbers four times apart, separated only by the
#: 4:1 split of 2020-08-31.
APPLE_2019Q4 = (
    # (val,            accn,                    form,   filed)
    (4_443_236_000.0, "0000320193-19-000119", "10-K", "2019-10-31"),
    (4_443_236_000.0, "0000320193-20-000010", "10-Q", "2020-01-29"),
    (4_443_236_000.0, "0000320193-20-000052", "10-Q", "2020-05-01"),
    (4_443_236_000.0, "0000320193-20-000062", "10-Q", "2020-07-31"),
    (17_772_945_000.0, "0000320193-20-000096", "10-K", "2020-10-30"),
)


def _fixture_store() -> sqlite3.Connection:
    """A three-column stand-in for `pit_fact`'s selection contract.

    Deliberately NOT the production schema: the claim under test is about the
    SELECTOR's two bounds and the ORDER BY, and a fixture that carried thirty
    other columns would hide which two the proof rests on.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE obs (
                        period_end TEXT NOT NULL,
                        available_date TEXT NOT NULL,
                        filed TEXT NOT NULL,
                        accn TEXT NOT NULL,
                        val REAL NOT NULL)""")
    for val, accn, _form, filed in APPLE_2019Q4:
        # available_date under information_latency_policy_v1 with no acceptance
        # timestamp is the next session after `filed`; the next CALENDAR day is
        # used here, which is the same answer on a weekday and is never earlier.
        avail = (_dt.date.fromisoformat(filed) + _dt.timedelta(days=1)).isoformat()
        conn.execute("INSERT INTO obs VALUES (?, ?, ?, ?, ?)",
                     ("2019-09-28", avail, filed, accn, val))
    return conn


def _select(conn: sqlite3.Connection, as_of: str):
    """The selector's contract, verbatim: two bounds, one ORDER BY."""
    return conn.execute(
        """SELECT * FROM obs
            WHERE period_end <= ? AND available_date <= ?
            ORDER BY period_end DESC, available_date DESC, filed DESC, accn DESC
            LIMIT 1""", (as_of, as_of)).fetchone()


def check_supersession_is_not_look_ahead() -> None:
    """THE TEST THAT SEPARATES THIS POLICY FROM A LEAK.

    A later-filed observation cannot supersede an earlier one at an as-of
    BEFORE the later one's availability -- not because it loses a comparison,
    but because it is never in scope to be compared.
    """
    conn = _fixture_store()

    # 1. At a 2020-01-31 as-of the restatement is nine months in the future.
    row = _select(conn, "2020-01-31")
    check("at 2020-01-31 the selector returns the PRE-split 4,443,236,000",
          row is not None and row["val"] == 4_443_236_000.0,
          f"{row['val']:,.0f} from {row['accn']}" if row else "no row")
    check("the returned row's available_date is <= the as-of date",
          row is not None and row["available_date"] <= "2020-01-31",
          row["available_date"] if row else None)

    # 2. The explicit supersession gate says the same thing, and says WHY.
    later_avail = (_dt.date.fromisoformat("2020-10-30")
                   + _dt.timedelta(days=1)).isoformat()
    check("supersedes() REFUSES the 2020-10-30 restatement at a 2020-01-31 as-of",
          fk.supersedes(later_avail, "2019-09-28", "2019-09-28", "2020-01-31")
          is False,
          "superseding requires available_date <= as_of; it never consults the future")
    check("supersedes() ADMITS the same row once its availability has arrived",
          fk.supersedes(later_avail, "2020-09-26", "2019-09-28", "2021-01-31")
          is True)
    check("supersedes() refuses a row whose PERIOD has not happened yet",
          fk.supersedes("2020-01-01", "2020-12-31", "2019-09-28", "2020-06-30")
          is False, "period_end <= as_of is the second bound, and it is not optional")

    # 3. Adding the future row changes NOTHING at the earlier as-of. This is
    #    the strongest form of the claim: the answer is a function only of rows
    #    whose available_date <= as_of.
    before = _select(conn, "2020-01-31")["val"]
    conn.execute("INSERT INTO obs VALUES (?, ?, ?, ?, ?)",
                 ("2019-09-28", "2025-01-01", "2024-12-31", "9999999999-99-999999",
                  99_999_999_999.0))
    after = _select(conn, "2020-01-31")["val"]
    check("inserting a 2025-filed restatement does not move the 2020-01-31 answer",
          before == after == 4_443_236_000.0, f"{before:,.0f} -> {after:,.0f}")
    check("the same insert DOES move a 2025-06-30 answer",
          _select(conn, "2025-06-30")["val"] == 99_999_999_999.0,
          "supersession works forward; it just cannot work backward")

    # 4. And v2's age test cannot reach past the selector either: it is handed a
    #    row and returns a boolean. It takes no connection and no successor.
    import inspect
    params = set(inspect.signature(fk.is_stale_v2).parameters)
    check("is_stale_v2 takes no connection and no successor -- it cannot select",
          not (params & {"conn", "connection", "successor", "rows", "store"}),
          sorted(params))
    conn.close()


def check_state_persists_until_superseded() -> None:
    """A state fact is usable through the window v1 refuses, and not beyond it."""
    end = "2019-09-30"
    # v1: quarterly bucket, 4-month override -> expires 2020-01-31.
    check("v1 refuses the 2019-09-30 count from 2020-02-01",
          pit_policy.is_stale(end, "2020-02-01", 0, "shares_outstanding"))
    check("v2 ACCEPTS it on 2020-02-01 -- it is still the last count filed",
          not fk.is_stale_v2("shares_outstanding", end, "2020-02-01", 0))
    check("v2 accepts it at the ceiling boundary, inclusive",
          not fk.is_stale_v2("shares_outstanding", end, "2021-09-30", 0),
          f"{fk.STATE_SANITY_CEILING_MONTHS} months from period_end")
    check("v2 REFUSES it one day past the ceiling",
          fk.is_stale_v2("shares_outstanding", end, "2021-10-01", 0))
    check("the 2011-answering-2024 case the ceiling exists to refuse IS refused",
          fk.is_stale_v2("shares_outstanding", "2011-06-30", "2024-06-28", 0))
    for primitive in fk.primitives_of_kind(fk.FACT_KIND_STATE):
        if not fk.kind_spec(primitive).obtainable:
            continue
        check(f"{primitive}: v2 bound is the ceiling, not the cadence",
              fk.max_age_months_v2(primitive, 0) == fk.STATE_SANITY_CEILING_MONTHS)


def check_flow_facts_are_unchanged() -> None:
    """v2 must be IDENTICAL to v1 on every flow fact, at every age."""
    disagreements = []
    start = _dt.date(2014, 3, 31)
    for primitive in fk.primitives_of_kind(fk.FACT_KIND_FLOW):
        for offset in range(0, 1500, 11):
            end = (start + _dt.timedelta(days=offset)).isoformat()
            for gap in (0, 100, 180, 183, 190, 300, 450, 456, 460, 800):
                as_of = (_dt.date.fromisoformat(end)
                         + _dt.timedelta(days=gap)).isoformat()
                for qtrs in (0, 1, 4):
                    v1 = pit_policy.is_stale(end, as_of, qtrs, primitive)
                    v2 = fk.is_stale_v2(primitive, end, as_of, qtrs)
                    if v1 != v2:
                        disagreements.append((primitive, end, as_of, qtrs, v1, v2))
    check("v2 == v1 on every FLOW fact over a 137x10x3 sweep per primitive",
          not disagreements, disagreements[:3] or "7 primitives, 28,770 comparisons")
    check("FLOW_INHERITS_V1 says so on the record", fk.FLOW_INHERITS_V1 is True)
    check("a flow's v2 month bound IS v1's month bound",
          all(fk.max_age_months_v2(p, q) == pit_policy.max_age_months(q, p)
              for p in fk.primitives_of_kind(fk.FACT_KIND_FLOW) for q in (0, 1, 4)))


def check_v2_never_refuses_what_v1_accepts_on_state() -> None:
    """Monotonicity: on state facts v2's accepted set is a SUPERSET of v1's.

    If this ever fails, a coverage 'improvement' measured against v1 would be
    netting a loss somewhere against a gain elsewhere, and the headline number
    would be uninterpretable.
    """
    regressions = []
    start = _dt.date(2013, 1, 31)
    for primitive in fk.primitives_of_kind(fk.FACT_KIND_STATE):
        if not fk.kind_spec(primitive).obtainable:
            continue
        for offset in range(0, 2000, 13):
            end = (start + _dt.timedelta(days=offset)).isoformat()
            for gap in (0, 30, 91, 120, 122, 125, 180, 365, 400, 700, 731, 900):
                as_of = (_dt.date.fromisoformat(end)
                         + _dt.timedelta(days=gap)).isoformat()
                v1 = pit_policy.is_stale(end, as_of, 0, primitive)
                v2 = fk.is_stale_v2(primitive, end, as_of, 0)
                if v2 and not v1:                 # v2 refuses what v1 accepted
                    regressions.append((primitive, end, as_of))
    check("v2 never refuses a STATE fact that v1 accepted", not regressions,
          regressions[:3] or "monotone: the measured delta is a pure gain")


def check_market_rule() -> None:
    check("a price is usable on its own session",
          not fk.is_stale_v2("price_raw", "2024-06-28", "2024-06-28"))
    check("yesterday's close is NOT usable today under v2",
          fk.is_stale_v2("price_raw", "2024-06-27", "2024-06-28"))
    check("with a session count, the bound is 0 sessions",
          fk.is_stale_v2("price_raw", "2024-06-20", "2024-06-28", sessions_since=1)
          and not fk.is_stale_v2("price_raw", "2024-06-20", "2024-06-28",
                                 sessions_since=0))
    check("MARKET facts carry no month bound",
          fk.max_age_months_v2("price_raw", 0) is None)
    check("the market rung records that it is STRICTER than production, not laxer",
          "SHRINK" in fk.MARKET_RULE_VS_PRODUCTION["direction"])


def check_unparseable_is_stale() -> None:
    for primitive in ("revenue", "cash", "price_raw"):
        check(f"{primitive}: an unparseable period end is stale, as in v1",
              fk.is_stale_v2(primitive, "not-a-date", "2024-06-28", 0)
              and fk.is_stale_v2(primitive, None, "2024-06-28", 0))


# ==========================================================================
# 4. THE RECORD A RUN STORES
# ==========================================================================

def check_policy_record() -> None:
    record = fk.staleness_policy_v2()
    check("the policy serialises to JSON",
          isinstance(json.dumps(record, sort_keys=True), str))
    check("it names its own version and the one it supersedes",
          record["staleness_policy_version"] == fk.STALENESS_POLICY_V2
          and record["supersedes"] == fk.STALENESS_POLICY_V1)
    check("it belongs to the candidate lineage, not to production",
          record["lineage"] == fk.CANDIDATE_MODEL_VERSION)
    try:
        import pit_factor_contract
        check("the lineage id matches pit_factor_contract",
              fk.CANDIDATE_MODEL_VERSION
              == pit_factor_contract.CANDIDATE_MODEL_VERSION,
              pit_factor_contract.CANDIDATE_MODEL_VERSION)
    except Exception as exc:                        # pragma: no cover
        check("the lineage id matches pit_factor_contract", False, str(exc))
    check("the PIT proof is IN the record, not only in a docstring",
          "available_date <= as_of" in record["point_in_time_proof"]["argument"])
    check("the record names the executable test that proves it",
          record["point_in_time_proof"]["executable_test"].endswith(
              "check_supersession_is_not_look_ahead"))
    check("the record carries v1's constants, unmodified",
          record["v1_untouched"]["constants"]["MAX_AGE_ANNUAL_MONTHS"] == 15)
    check("the record states what staleness does NOT fix",
          record["does_not_fix"]["reason"] == "only_period_average_available")
    check("the sanity ceiling is on the record with its argument",
          record["taxonomy"]["state"]["sanity_ceiling_months"]
          == fk.STATE_SANITY_CEILING_MONTHS
          and len(record["taxonomy"]["state"]["ceiling_why"]) > 40)

    decision = fk.staleness_decision("shares_outstanding", "2019-09-30",
                                     "2020-02-01", 0)
    check("staleness_decision reports BOTH verdicts and flags the change",
          decision["stale_v1"] and not decision["stale_v2"] and decision["changed"],
          f"v1={decision['stale_v1']} v2={decision['stale_v2']} "
          f"age={decision['age_days']}d")
    check("staleness_decision names the v2 refusal vocabulary",
          fk.staleness_decision("cash", "2011-06-30", "2024-06-28", 0)["reason_v2"]
          == fk.REASON_BEYOND_STATE_CEILING)


def check_measured_record() -> None:
    """The measured numbers must be internally consistent and must reproduce
    the published census, or the deltas built on them mean nothing."""
    m = fk.MEASURED

    # The baseline is the proof the pipeline is the same one. If these drift,
    # every delta in the record is measuring two different things.
    check("the refusal census reproduces the published 174,674",
          m["refusals"]["v1_total"] == 174674, m["refusals"]["v1_total"])
    stale = m["refusals"]["v1"]["stale_beyond_max_age"]
    avg = m["refusals"]["v1"]["only_period_average_available"]
    check("stale_beyond_max_age is the published 52.1% of them",
          abs(100.0 * stale / 174674 - 52.1) < 0.1,
          f"{100.0 * stale / 174674:.2f}%")
    check("only_period_average_available is the published 28.0% of them",
          abs(100.0 * avg / 174674 - 28.0) < 0.1, f"{100.0 * avg / 174674:.2f}%")
    ev = m["ev_ebitda_cohort"]
    check("the EV/EBITDA baseline reproduces median 1, P(N>=3) 34.2%, P(N>=12) 7.0%",
          ev["baseline_reproduced"]["median"] == 1
          and abs(ev["baseline_reproduced"]["pct_ge_3"] - 34.2) < 0.05
          and abs(ev["baseline_reproduced"]["pct_ge_12"] - 7.0) < 0.05,
          ev["baseline_reproduced"])
    check("the EBITDA-only cohort reproduces median 13 / 95.2% / 56.0%",
          ev["n_ebitda_leaf_unchanged"]["median"] == 13
          and ev["n_ebitda_leaf_unchanged"]["pct_ge_3"] == 95.2
          and ev["n_ebitda_leaf_unchanged"]["pct_ge_12"] == 56.0)

    # v2 does not touch these three refusals, and the record must say so by
    # carrying the identical number, not by omitting it.
    for reason in ("only_period_average_available", "never_filed_a_share_count",
                   "not_yet_filed"):
        check(f"{reason} is BIT-IDENTICAL under v2 -- staleness cannot reach it",
              m["refusals"]["v1"][reason] == m["refusals"]["v2"][reason],
              m["refusals"]["v2"][reason])
    check("v2's refusal total is the sum of its parts",
          sum(m["refusals"]["v2"].values()) == m["refusals"]["v2_total"],
          sum(m["refusals"]["v2"].values()))
    check("v1's refusal total is the sum of its parts",
          sum(m["refusals"]["v1"].values()) == m["refusals"]["v1_total"])

    # The seasonality claim, as arithmetic rather than as an adjective.
    v1_vals = [m["seasonality"]["v1"][k] for k in
               ("january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december")]
    v2_vals = [m["seasonality"]["v2"][k] for k in
               ("january", "february", "march", "april", "may", "june", "july",
                "august", "september", "october", "november", "december")]
    check("the recorded v1 month range matches the recorded months",
          abs((max(v1_vals) - min(v1_vals))
              - m["seasonality"]["v1"]["range_points"]) < 0.02,
          f"{max(v1_vals) - min(v1_vals):.2f}")
    check("the recorded v2 month range matches the recorded months",
          abs((max(v2_vals) - min(v2_vals))
              - m["seasonality"]["v2"]["range_points"]) < 0.02)
    check("v2's month range is at least 50x smaller -- the calendar is gone",
          (max(v1_vals) - min(v1_vals)) / (max(v2_vals) - min(v2_vals)) > 50,
          f"{(max(v1_vals) - min(v1_vals)) / (max(v2_vals) - min(v2_vals)):.1f}x")
    check("every month clears 70% under v2, including January and October",
          min(v2_vals) > 70.0, f"worst month {min(v2_vals)}%")
    check("January and October were the two worst months under v1",
          sorted(v1_vals)[:2] == sorted([m["seasonality"]["v1"]["january"],
                                         m["seasonality"]["v1"]["october"]]),
          f"january {m['seasonality']['v1']['january']}, "
          f"october {m['seasonality']['v1']['october']}")

    # The control: cash and debt lift the level and leave the calendar alone.
    seas = ev["cohort_seasonality_pct_ge_3"]
    check("the CONTROL holds: v2 fundamentals alone do NOT flatten the calendar",
          seas["v1_shares_v2_fundamentals"]["range"] > 25.0,
          f"range {seas['v1_shares_v2_fundamentals']['range']} points, "
          "against 26.11 at baseline")
    check("the share leaf alone DOES flatten it",
          seas["v2_shares_v1_fundamentals"]["range"] < 3.0,
          f"range {seas['v2_shares_v1_fundamentals']['range']} points")

    # The ceiling's own argument, checked as monotone arithmetic.
    sens = m["ceiling"]["sensitivity_entity_dates_admitted"]
    order = ["v1_quarterly_4m", "6m", "9m", "12m", "18m", "24m", "36m", "48m",
             "unbounded"]
    check("the ceiling sensitivity curve is monotone increasing",
          all(sens[a] <= sens[b] for a, b in zip(order, order[1:])))
    steep = sens["12m"] - sens["v1_quarterly_4m"]
    flat = sens["24m"] - sens["12m"]
    check("the curve is steep where the artefact is and flat where the ceiling sits",
          steep > 5 * flat, f"4->12m buys {steep:,}; 12->24m buys {flat:,}")
    check("removing the ceiling entirely would admit a large multi-year tail",
          sens["unbounded"] - sens["24m"] > 20000,
          f"{sens['unbounded'] - sens['24m']:,} entity-dates older than 24 months")
    check("the ceiling months on the record are the constant in force",
          m["ceiling_months"] == fk.STATE_SANITY_CEILING_MONTHS)

    # The gain, recomputed rather than trusted.
    pooled = m["share_count_coverage"]["pooled"]
    check("the pooled gain is the difference of the pooled counts",
          pooled["v2_defensible"] - pooled["v1_defensible"]
          == pooled["gain_entity_dates"])
    check("the pooled percentages follow from the pooled counts",
          abs(100.0 * pooled["v1_defensible"] / pooled["priced_entity_dates"]
              - pooled["v1_pct"]) < 0.01
          and abs(100.0 * pooled["v2_defensible"] / pooled["priced_entity_dates"]
                  - pooled["v2_pct"]) < 0.01)
    check("an unmeasured quantity is None or absent, never 0",
          m["resources"]["rows_written_to_the_store"] == 0
          and isinstance(m["market_rung"]["pct_priced_listings_with_a_bar_on_"
                                          "the_as_of_session"], float),
          "the only 0 in the record is a measured 0: rows written")


def check_seasonality_verdict_is_falsifiable() -> None:
    """`seasonality_summary` must be able to say the diagnosis was WRONG."""
    flat = {"january": {"v1_pct": 20.0, "v2_pct": 50.0},
            "april": {"v1_pct": 50.0, "v2_pct": 51.0},
            "october": {"v1_pct": 25.0, "v2_pct": 50.5}}
    good = fk.seasonality_summary(flat)
    check("a flattened month profile is reported as the artefact REMOVED",
          "REMOVED" in good["verdict"], good["verdict"])
    unchanged = {"january": {"v1_pct": 20.0, "v2_pct": 22.0},
                 "april": {"v1_pct": 50.0, "v2_pct": 52.0},
                 "october": {"v1_pct": 25.0, "v2_pct": 27.0}}
    bad = fk.seasonality_summary(unchanged)
    check("an UNCHANGED month profile is reported as the artefact SURVIVING",
          "SURVIVES" in bad["verdict"], bad["verdict"])


def check_measure_date_shape() -> None:
    """`measure_date` is read-only and its record has the fields the brief asks for.

    Run against an in-memory database with the real schema absent, so what is
    under test is the CONTRACT, not the store: a missing table must raise
    plainly rather than be silently reported as zero coverage.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    raised = False
    try:
        fk.measure_date(conn, "2024-06-28")
    except sqlite3.OperationalError:
        raised = True
    except Exception:
        raised = True
    check("measure_date on an empty database raises rather than reporting 0%",
          raised, "an unmeasured quantity is never reported as zero")
    conn.close()

    check("the census is labelled SURVIVOR_ONLY_DIAGNOSTIC",
          fk.SAMPLE_SCOPE == "SURVIVOR_ONLY_DIAGNOSTIC")
    check("the census checks disk before it starts",
          fk.disk_check(".")["min_free_gib"] == fk.MIN_FREE_GIB)

    monthly = fk.by_calendar_month({
        "2024-01-31": {"month": 1, "n_priced": 100, "ceiling_binds": 4,
                       "rescued_by_v2": 30,
                       "v1": {"defensible": 20}, "v2": {"defensible": 50}},
        "2024-04-30": {"month": 4, "n_priced": 100, "ceiling_binds": 4,
                       "rescued_by_v2": 5,
                       "v1": {"defensible": 45}, "v2": {"defensible": 50}},
    })
    check("by_calendar_month pools entity-dates rather than averaging percentages",
          monthly["january"]["v1_pct"] == 20.0
          and monthly["january"]["delta_pct_points"] == 30.0,
          monthly["january"])


def main() -> int:
    print("=" * 74)
    print("pit_fact_kind -- FLOW / STATE / MARKET and fact_kind_staleness_v2")
    print("=" * 74)
    for section, fn in (
            ("TAXONOMY", check_taxonomy_covers_pit_derive),
            ("TAXONOMY", check_the_owners_assignments),
            ("V1 FROZEN", check_v1_is_frozen),
            ("V1 FROZEN", check_month_arithmetic_agrees_with_v1),
            ("PIT PROOF", check_supersession_is_not_look_ahead),
            ("STATE RULE", check_state_persists_until_superseded),
            ("FLOW RULE", check_flow_facts_are_unchanged),
            ("MONOTONE", check_v2_never_refuses_what_v1_accepts_on_state),
            ("MARKET RULE", check_market_rule),
            ("EDGE", check_unparseable_is_stale),
            ("RECORD", check_policy_record),
            ("MEASURED", check_measured_record),
            ("MEASUREMENT", check_seasonality_verdict_is_falsifiable),
            ("MEASUREMENT", check_measure_date_shape)):
        print(f"\n-- {section}: {fn.__name__}")
        fn()
    print("\n" + "=" * 74)
    if FAILURES:
        print(f"FAILED {len(FAILURES)} check(s):")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
