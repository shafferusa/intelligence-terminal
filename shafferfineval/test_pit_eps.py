"""Plain-script tests for pit_eps. No pytest, no network, no production store.

    python test_pit_eps.py

Every check runs against a throwaway SQLite file in the system temp directory.
Nonzero exit on any failure.

The check this file exists for is `check_abs_pathology_is_not_reproduced`.
Everything else guards a schema or a policy; that one guards the single error
that would silently invert the valuation factor.
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import traceback
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_eps
import pit_policy
import pit_store

FAILURES: list[str] = []
PASSES: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSES.append(name)
    else:
        FAILURES.append(f"{name}: {detail}" if detail else name)


def fresh_db() -> tuple[sqlite3.Connection, str]:
    path = os.path.join(tempfile.mkdtemp(prefix="pit_eps_test_"), "eps.db")
    conn = pit_store.init_db(path)
    pit_eps.ensure_schema(conn)
    return conn, path


def obs(entity_id: int, concept: str, tag: str, start: str, end: str, qtrs: int,
        val: float, accn: str, filed: str, available: str,
        form: str = "10-Q") -> tuple[Any, ...]:
    return (entity_id, concept, "us-gaap", tag, pit_eps.UNIT_USD_PER_SHARE,
            start, end, qtrs, val, pit_eps.sign_case(val), accn, form, None,
            None, filed, None, None, available, "next_session_no_acceptance_time",
            pit_policy.LATENCY_POLICY_VERSION, pit_eps.EPS_LADDER_VERSION,
            "test", "2026-09-21T00:00:00+00:00")


# --------------------------------------------------------------------------
# (1) THE SIGN VOCABULARY
# --------------------------------------------------------------------------

def check_sign_cases() -> None:
    check("sign positive", pit_eps.sign_case(3.25) == pit_eps.SIGN_POSITIVE)
    check("sign zero is exact",
          pit_eps.sign_case(0.0) == pit_eps.SIGN_ZERO)
    check("sign negative", pit_eps.sign_case(-0.01) == pit_eps.SIGN_NEGATIVE,
          "a one-cent LOSS is loss-making, not near-zero-positive")
    check("sign near zero at the boundary",
          pit_eps.sign_case(0.01) == pit_eps.SIGN_NEAR_ZERO_POSITIVE,
          "one cent is the reporting granularity and is inclusive")
    check("sign ordinary just above the boundary",
          pit_eps.sign_case(0.02) == pit_eps.SIGN_POSITIVE)
    try:
        pit_eps.sign_case(None)
        check("sign refuses None", False, "None was accepted as a sign case")
    except ValueError:
        check("sign refuses None", True)
    try:
        pit_eps.sign_case(float("nan"))
        check("sign refuses NaN", False)
    except ValueError:
        check("sign refuses NaN", True)

    check("pe leg positive",
          pit_eps.pe_leg_for(pit_eps.SIGN_POSITIVE) == pit_eps.PE_AVAILABLE)
    check("pe leg near zero is flagged extreme",
          pit_eps.pe_leg_for(pit_eps.SIGN_NEAR_ZERO_POSITIVE)
          == pit_eps.PE_AVAILABLE_EXTREME)
    check("pe leg zero is undefined",
          pit_eps.pe_leg_for(pit_eps.SIGN_ZERO) == pit_eps.PE_UNAVAILABLE_ZERO)
    check("pe leg negative is unavailable",
          pit_eps.pe_leg_for(pit_eps.SIGN_NEGATIVE) == pit_eps.PE_UNAVAILABLE_LOSS)


def check_abs_pathology_is_not_reproduced() -> None:
    """THE check. |P/E| ranks the worst loss-makers as the cheapest securities.

    Three issuers, all at $10:
        A earns  $2.00/share  -> P/E   5   genuinely cheap
        B earns  $0.10/share  -> P/E 100   expensive
        C loses  $5.00/share  -> P/E  -2, and |P/E| = 2, which sorts BELOW A.

    A lower-is-better valuation percentile fed |P/E| therefore puts the company
    burning half its market value a year at the top of the value screen. The
    test asserts the refusal, and asserts the ordering it protects.
    """
    a = pit_eps.price_earnings(10.0, 2.00)
    b = pit_eps.price_earnings(10.0, 0.10)
    c = pit_eps.price_earnings(10.0, -5.00)

    check("pe computed for a profitable issuer", a["pe"] == 5.0, str(a))
    check("pe computed for a thin-margin issuer", b["pe"] == 100.0, str(b))
    check("pe REFUSED for a loss-maker", c["pe"] is None, str(c))
    check("pe refusal names the reason",
          c["reason"] == pit_eps.PE_UNAVAILABLE_LOSS, str(c["reason"]))
    check("loss-maker never sorts as cheapest",
          abs(-5.0) and c["pe"] is None,
          "if this ever returns a number, |P/E| = 2 undercuts the genuinely "
          "cheap issuer at 5 and the value screen is inverted")
    check("zero eps is undefined, not infinite",
          pit_eps.price_earnings(10.0, 0.0)["pe"] is None)
    near = pit_eps.price_earnings(30.0, 0.01)
    check("near-zero eps is available but flagged extreme",
          near["pe"] == 3000.0 and near["extreme"] is True, str(near))

    # The signed earnings yield is the quantity that survives the boundary.
    ys = [pit_eps.signed_earnings_yield(e, 10.0)["earnings_yield"]
          for e in (-5.0, -0.1, 0.0, 0.1, 2.0)]
    check("earnings yield is monotone through zero",
          ys == sorted(ys) and ys[0] < 0 < ys[-1], str(ys))
    check("earnings yield is labelled a research candidate",
          pit_eps.signed_earnings_yield(1.0, 10.0)["layer"]
          == "ML_RESEARCH_CANDIDATE")


# --------------------------------------------------------------------------
# (2) SCHEMA INVARIANTS
# --------------------------------------------------------------------------

def check_schema_invariants() -> None:
    conn, path = fresh_db()
    try:
        entity = pit_store.upsert_entity(conn, "0000000001")
        rows = [
            obs(entity, pit_eps.CONCEPT_DILUTED, "EarningsPerShareDiluted",
                "2019-01-01", "2019-12-31", 4, -1.75, "acc-1", "2020-02-14",
                "2020-02-18", form="10-K"),
            # SAME accession, SAME period end, DIFFERENT qtrs: a 10-K carries
            # the Q4 figure and the full-year figure side by side.
            obs(entity, pit_eps.CONCEPT_DILUTED, "EarningsPerShareDiluted",
                "2019-10-01", "2019-12-31", 1, -0.40, "acc-1", "2020-02-14",
                "2020-02-18", form="10-K"),
        ]
        written = pit_eps.insert_eps_obs(conn, rows)
        conn.commit()
        check("a NEGATIVE eps is storable", written == 2,
              f"wrote {written}; a CHECK (val >= 0) would delete every loss-maker")
        stored = conn.execute(
            "SELECT COUNT(*) FROM pit_eps_obs WHERE period_end = '2019-12-31'"
        ).fetchone()[0]
        check("qtrs is in the unique key", stored == 2,
              f"{stored} rows; without qtrs the annual and Q4 figures collide")
        neg = conn.execute(
            "SELECT val, sign_case FROM pit_eps_obs WHERE qtrs = 4").fetchone()
        check("the negative value survives unchanged",
              neg[0] == -1.75 and neg[1] == pit_eps.SIGN_NEGATIVE, str(tuple(neg)))

        try:
            conn.execute("UPDATE pit_eps_obs SET val = 9.99")
            conn.commit()
            check("pit_eps_obs refuses UPDATE", False, "the update succeeded")
        except sqlite3.IntegrityError:
            conn.rollback()
            check("pit_eps_obs refuses UPDATE", True)
        try:
            conn.execute("DELETE FROM pit_eps_obs")
            conn.commit()
            check("pit_eps_obs refuses DELETE", False, "the delete succeeded")
        except sqlite3.IntegrityError:
            conn.rollback()
            check("pit_eps_obs refuses DELETE", True)

        # available_date < filed must be impossible.
        try:
            bad = obs(entity, pit_eps.CONCEPT_DILUTED, "EarningsPerShareDiluted",
                      "2018-01-01", "2018-12-31", 4, 1.0, "acc-x", "2019-02-14",
                      "2019-02-01")
            conn.execute(
                "INSERT INTO pit_eps_obs (" + ", ".join(pit_eps.EPS_COLUMNS)
                + ") VALUES (" + ", ".join("?" * len(pit_eps.EPS_COLUMNS)) + ")",
                bad)
            conn.commit()
            check("available_date >= filed is enforced", False)
        except sqlite3.IntegrityError:
            conn.rollback()
            check("available_date >= filed is enforced", True)

        check("ensure_schema is idempotent",
              pit_eps.ensure_schema(conn) == "exists")
    finally:
        conn.close()


# --------------------------------------------------------------------------
# (3) THE DERIVED TTM
# --------------------------------------------------------------------------

def _load_quarters(conn: sqlite3.Connection, entity: int,
                   values: list[tuple[str, str, float]], accn: str,
                   filed: str, available: str) -> None:
    rows = [obs(entity, pit_eps.CONCEPT_DILUTED, "EarningsPerShareDiluted",
                start, end, 1, val, accn, filed, available)
            for start, end, val in values]
    pit_eps.insert_eps_obs(conn, rows)
    conn.commit()


def check_ttm_derivation() -> None:
    conn, path = fresh_db()
    try:
        entity = pit_store.upsert_entity(conn, "0000000002")
        # Four calendar quarters of 2019 -- each filed with its own accession.
        quarters = [
            ("2019-01-01", "2019-03-31", 0.50, "q1", "2019-04-25", "2019-04-26"),
            ("2019-04-01", "2019-06-30", 0.60, "q2", "2019-07-25", "2019-07-26"),
            ("2019-07-01", "2019-09-30", 0.70, "q3", "2019-10-25", "2019-10-28"),
            ("2019-10-01", "2019-12-31", 0.80, "q4", "2020-01-28", "2020-01-29"),
        ]
        for start, end, val, accn, filed, avail in quarters:
            _load_quarters(conn, entity, [(start, end, val)], accn, filed, avail)

        result = pit_eps.derive_ttm_for_entity(conn, entity, commit=True)
        rows = conn.execute(
            "SELECT period_end, ttm_eps, method, n_components, available_date "
            "FROM pit_eps_ttm ORDER BY period_end").fetchall()
        by_period = {r[0]: r for r in rows}

        check("no TTM from three quarters",
              "2019-09-30" not in by_period,
              "a partial sum is a wrong number, not a partial one")
        check("a four-quarter TTM exists at the fourth quarter",
              "2019-12-31" in by_period, str(list(by_period)))
        if "2019-12-31" in by_period:
            row = by_period["2019-12-31"]
            check("four-quarter TTM sums the four quarters",
                  abs(row[1] - 2.60) < 1e-9, f"got {row[1]}")
            check("four-quarter TTM names its method",
                  row[2] == pit_eps.METHOD_FOUR_QUARTERS, row[2])
            check("four-quarter TTM has four components", row[3] == 4, str(row[3]))
            check("TTM available_date is the MAX over components",
                  row[4] == "2020-01-29", row[4])

        # The ANNUAL figure at the same period end must WIN the method choice.
        pit_eps.insert_eps_obs(conn, [
            obs(entity, pit_eps.CONCEPT_DILUTED, "EarningsPerShareDiluted",
                "2019-01-01", "2019-12-31", 4, 2.55, "fy19", "2020-02-14",
                "2020-02-18", form="10-K")])
        conn.commit()
        conn.execute("DROP TRIGGER trg_pit_eps_ttm_no_update")
        conn.execute("DELETE FROM pit_eps_ttm")
        conn.commit()
        pit_eps.derive_ttm_for_entity(conn, entity, commit=True)
        row = conn.execute(
            "SELECT ttm_eps, method, n_components FROM pit_eps_ttm "
            "WHERE period_end = '2019-12-31'").fetchone()
        check("the filed ANNUAL figure wins over a four-quarter sum",
              row is not None and row[1] == pit_eps.METHOD_ANNUAL, str(tuple(row) if row else None))
        check("the annual figure is the audited number, not the sum",
              row is not None and abs(row[0] - 2.55) < 1e-9,
              f"got {row[0] if row else None}; the quarters sum to 2.60 because "
              "each is divided by its own weighted average share count")
        check("the annual TTM has one component",
              row is not None and row[2] == 1)
    finally:
        conn.close()


def check_ttm_vintages_and_selection() -> None:
    """A restatement is a NEW row, and the PIT selector never reads it early."""
    conn, path = fresh_db()
    try:
        entity = pit_store.upsert_entity(conn, "0000000003")
        pit_eps.insert_eps_obs(conn, [
            obs(entity, pit_eps.CONCEPT_DILUTED, "EarningsPerShareDiluted",
                "2019-01-01", "2019-12-31", 4, 2.00, "orig", "2020-02-14",
                "2020-02-18", form="10-K"),
            obs(entity, pit_eps.CONCEPT_DILUTED, "EarningsPerShareDiluted",
                "2019-01-01", "2019-12-31", 4, 1.20, "restate", "2021-03-01",
                "2021-03-02", form="10-K/A"),
        ])
        conn.commit()
        pit_eps.derive_ttm_for_entity(conn, entity, commit=True)
        n = conn.execute("SELECT COUNT(*) FROM pit_eps_ttm").fetchone()[0]
        check("a restatement produces a SECOND TTM row", n == 2, f"{n} rows")

        before = pit_eps.ttm_eps_as_of_detail(conn, entity, "2020-06-30")
        after = pit_eps.ttm_eps_as_of_detail(conn, entity, "2021-03-31")
        check("PIT selection returns the ORIGINAL before the restatement lands",
              before["available"] and abs(before["ttm_eps"] - 2.00) < 1e-9,
              str(before["ttm_eps"]))
        check("PIT selection returns the RESTATEMENT once it has landed",
              after["available"] and abs(after["ttm_eps"] - 1.20) < 1e-9,
              str(after["ttm_eps"]))

        early = pit_eps.ttm_eps_as_of_detail(conn, entity, "2020-01-31")
        check("nothing is knowable before its available_date",
              not early["available"]
              and early["reason"] == pit_eps.REASON_NOT_YET_FILED,
              str(early["reason"]))

        # 15-month annual bound: FY2019 is usable through 2021-03-31 and stale
        # from 2021-04-01. That is the ANNUAL budget, deliberately NOT the
        # four-month shares_outstanding override.
        ok = pit_eps.ttm_eps_as_of_detail(conn, entity, "2021-03-31")
        stale = pit_eps.ttm_eps_as_of_detail(conn, entity, "2021-04-01")
        check("a TTM is usable to the 15-month boundary", ok["available"])
        check("a TTM is stale past the 15-month boundary",
              not stale["available"] and stale["reason"] == pit_eps.REASON_STALE,
              str(stale["reason"]))
        check("the TTM staleness bound is the ANNUAL 15 months",
              pit_policy.max_age_months(4, pit_eps.STALENESS_CONCEPT) == 15,
              "a 4-month bound is the shares_outstanding override that collapses "
              "the valuation factor every January and October")
    finally:
        conn.close()


def check_reaffirmation_is_not_a_new_vintage() -> None:
    """Apple's real shape: one number re-filed twice, then a split restatement.

    CIK 320193's diluted EPS for the year ending 2019-09-28, as measured live:
    11.89 filed 2019-10-31, then 2.97 filed 2020-10-30 and 2.97 again filed
    2021-10-29. Three accessions, TWO answers. The derived table must carry two
    rows -- the split restatement changes what was knowable, the re-filing of
    an unchanged 2.97 does not -- while the PRIMITIVES keep all three.
    """
    conn, path = fresh_db()
    try:
        entity = pit_store.upsert_entity(conn, "0000000005")
        pit_eps.insert_eps_obs(conn, [
            obs(entity, pit_eps.CONCEPT_DILUTED, "EarningsPerShareDiluted",
                "2018-09-30", "2019-09-28", 4, 11.89, "a19", "2019-10-31",
                "2019-11-01", form="10-K"),
            obs(entity, pit_eps.CONCEPT_DILUTED, "EarningsPerShareDiluted",
                "2018-09-30", "2019-09-28", 4, 2.97, "a20", "2020-10-30",
                "2020-11-02", form="10-K"),
            obs(entity, pit_eps.CONCEPT_DILUTED, "EarningsPerShareDiluted",
                "2018-09-30", "2019-09-28", 4, 2.97, "a21", "2021-10-29",
                "2021-11-01", form="10-K"),
        ])
        conn.commit()
        result = pit_eps.derive_ttm_for_entity(conn, entity, commit=True)
        rows = conn.execute(
            "SELECT ttm_eps, accn_key, available_date FROM pit_eps_ttm "
            "ORDER BY available_date").fetchall()
        check("three primitive vintages are all stored",
              conn.execute("SELECT COUNT(*) FROM pit_eps_obs").fetchone()[0] == 3)
        check("a split restatement IS a new derived vintage",
              len(rows) == 2, f"{len(rows)} derived rows: {[tuple(r) for r in rows]}")
        check("the re-affirmation is counted, not stored",
              result["reaffirmations"] == 1, str(result["reaffirmations"]))
        if len(rows) == 2:
            check("the pre-split figure is the first vintage",
                  abs(rows[0][0] - 11.89) < 1e-9, str(rows[0][0]))
            check("the post-split figure is the second",
                  abs(rows[1][0] - 2.97) < 1e-9, str(rows[1][0]))
            check("the derived vintage names the accession that first published it",
                  rows[1][1] == "a20", rows[1][1])

        # The point-in-time consequence: the pre-split EPS is what a 2020-01-31
        # reader saw, and it must be paired with the RAW as-printed price.
        early = pit_eps.ttm_eps_as_of_detail(conn, entity, "2020-01-31")
        check("a pre-split as-of returns the pre-split EPS",
              early["available"] and abs(early["ttm_eps"] - 11.89) < 1e-9,
              str(early["ttm_eps"]))
        raw = pit_eps.price_earnings(309.51, early["ttm_eps"])
        adjusted = pit_eps.price_earnings(77.38, early["ttm_eps"])
        check("the raw price gives the true multiple",
              25.0 < raw["pe"] < 27.0, str(raw["pe"]))
        check("a split-adjusted price gives a 4x-wrong multiple",
              adjusted["pe"] < 7.0,
              "the mirror of pit_shares' split trap: a mega cap printing a "
              "deep-value multiple with no error anywhere")
    finally:
        conn.close()


def check_sign_crossing_and_ladder() -> None:
    conn, path = fresh_db()
    try:
        entity = pit_store.upsert_entity(conn, "0000000004")
        pit_eps.insert_eps_obs(conn, [
            obs(entity, pit_eps.CONCEPT_DILUTED, "EarningsPerShareDiluted",
                "2018-01-01", "2018-12-31", 4, 1.50, "fy18", "2019-02-14",
                "2019-02-18", form="10-K"),
            obs(entity, pit_eps.CONCEPT_DILUTED, "EarningsPerShareDiluted",
                "2019-01-01", "2019-12-31", 4, -0.75, "fy19", "2020-02-14",
                "2020-02-18", form="10-K"),
            # A basic figure for the same years -- the ladder must prefer diluted.
            obs(entity, pit_eps.CONCEPT_BASIC, "EarningsPerShareBasic",
                "2019-01-01", "2019-12-31", 4, -0.70, "fy19", "2020-02-14",
                "2020-02-18", form="10-K"),
        ])
        conn.commit()
        pit_eps.derive_ttm_for_entity(conn, entity, commit=True)
        crossing = conn.execute(
            "SELECT sign_crossing, prev_period_end, prev_ttm_eps FROM pit_eps_ttm "
            "WHERE concept_key = ? AND period_end = '2019-12-31'",
            (pit_eps.CONCEPT_DILUTED,)).fetchone()
        check("a profit-to-loss step is flagged sign_crossing",
              crossing is not None and crossing[0] == 1, str(tuple(crossing) if crossing else None))
        check("the crossing names the period it crossed from",
              crossing is not None and crossing[1] == "2018-12-31")

        first = conn.execute(
            "SELECT sign_crossing FROM pit_eps_ttm WHERE period_end = '2018-12-31' "
            "AND concept_key = ?", (pit_eps.CONCEPT_DILUTED,)).fetchone()
        check("the first period has nothing to cross from",
              first is not None and first[0] == 0)

        picked = pit_eps.ttm_eps_as_of_detail(conn, entity, "2020-06-30")
        check("the ladder prefers DILUTED over basic",
              picked["concept_key"] == pit_eps.CONCEPT_DILUTED,
              str(picked["concept_key"]))
        check("a loss is available as EARNINGS and unavailable as a MULTIPLE",
              picked["available"] and picked["pe_leg"] == pit_eps.PE_UNAVAILABLE_LOSS,
              f"available={picked['available']} leg={picked['pe_leg']}")
    finally:
        conn.close()


def check_policy_v1_is_untouched() -> None:
    """The EPS ladder must not have leaked into a frozen policy."""
    v1_tags = pit_policy.ladder_tags(pit_policy.LADDER_VERSION)
    v2_tags = pit_policy.ladder_tags(pit_policy.LADDER_VERSION_V2)
    eps_tags = {c.tag for c in pit_eps.EPS_CONCEPTS}
    check("concept_ladder_v1 names no EPS tag", not (v1_tags & eps_tags),
          str(sorted(v1_tags & eps_tags)))
    check("concept_ladder_v2 names no EPS tag", not (v2_tags & eps_tags),
          "v2's invariant is that it adds no tag, so the loaded store serves it "
          "with no re-ingest; three EPS tags would falsify that")
    check("the EPS ladder carries its own candidate version",
          pit_eps.EPS_LADDER_VERSION not in pit_policy.ladder_versions()
          and pit_eps.EPS_LADDER_VERSION.endswith("v2"))
    spec = pit_eps.eps_ladder_spec()
    check("the spec serialises", isinstance(spec["ladder"], list)
          and len(spec["ladder"]) == 3)
    check("the fetch plan is two concepts plus a conditional third",
          len(pit_eps.EPS_LADDER_PRIMARY) == 2
          and len(pit_eps.EPS_LADDER_FALLBACK) == 1)
    check("diluted leads the ladder",
          pit_eps.EPS_LADDER[0] == pit_eps.CONCEPT_DILUTED)
    check("basic is last and labelled as overstating EPS",
          pit_eps.EPS_LADDER[-1] == pit_eps.CONCEPT_BASIC
          and pit_eps.concept_for(pit_eps.CONCEPT_BASIC).bound
          == pit_eps.BOUND_OVERSTATES)


CHECKS: tuple[Callable[[], None], ...] = (
    check_sign_cases,
    check_abs_pathology_is_not_reproduced,
    check_schema_invariants,
    check_ttm_derivation,
    check_ttm_vintages_and_selection,
    check_reaffirmation_is_not_a_new_vintage,
    check_sign_crossing_and_ladder,
    check_policy_v1_is_untouched,
)


def main() -> int:
    for fn in CHECKS:
        try:
            fn()
        except Exception:
            FAILURES.append(f"{fn.__name__} raised:\n{traceback.format_exc()}")
    for name in PASSES:
        print(f"PASS  {name}")
    for name in FAILURES:
        print(f"FAIL  {name}")
    print(f"\n{len(PASSES)} passed, {len(FAILURES)} failed")
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
