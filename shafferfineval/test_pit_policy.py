"""Offline tests for the point-in-time research policies.

Pure stdlib, no network, no database:   python test_pit_policy.py

The checks that matter here are the ones that catch a LEAK -- a policy that
lets the replay see something a reader could not have seen on the day. The
15:30 boundary, the ASC 606 date gate and the staleness boundary are all of
that kind: each of them is a place where an off-by-one quietly flatters the
backtest instead of failing loudly.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pit_policy as P
import pit_store

fails: list[str] = []

#: The FROZEN serialisation of concept_ladder_v1, measured from the module on
#: 2026-09-21 immediately BEFORE concept_ladder_v2 was written. This is not a
#: style check. `pit_feature.ladder_version` stamps rows with a version id, and
#: `pit_feature_definition.tag_ladder_json` stores this exact JSON, so if v1's
#: bytes move, a row that says concept_ladder_v1 no longer identifies the rules
#: that produced it and the whole point of versioning is gone. v2 was built by
#: ADDING a ladder set beside v1, never by editing v1, and this digest is the
#: only thing that proves it.
V1_SPEC_BYTES = 13039
V1_SPEC_SHA256 = "84beac1c8a931b9c1bbf11a323864b17eab3b6e67464166225a12b507da39b02"
V1_BUNDLE_SHA256 = "6549b13ca85fd8e3d3c786355655cb4932544056b152a593ed5301fcbb24e563"


def check(name: str, cond: bool, extra: object = "") -> None:
    if cond:
        print(f"  PASS  {name}")
    else:
        print(f"  FAIL  {name} {extra}")
        fails.append(name)


# --------------------------------------------------------------------------
# A throwaway session calendar, standing in for pit_calendar. 2019 weekdays
# with 2019-07-04 (Independence Day) removed, so a real holiday is in play.
# --------------------------------------------------------------------------

_HOLIDAYS = {"2019-01-01", "2019-07-04", "2019-11-28", "2019-12-25",
             "2021-01-01", "2021-07-05", "2021-11-25", "2021-12-24"}


def _sessions(year_from: int = 2016, year_to: int = 2021) -> list[str]:
    day = dt.date(year_from, 1, 1)
    end = dt.date(year_to, 12, 31)
    out = []
    while day <= end:
        iso = day.isoformat()
        if day.weekday() < 5 and iso not in _HOLIDAYS:
            out.append(iso)
        day += dt.timedelta(days=1)
    return out


SESSIONS = _sessions()
SESSION_SET = set(SESSIONS)


def next_session(day: str) -> str | None:
    """First session ON OR AFTER `day`; None past the end of the calendar."""
    probe = dt.date.fromisoformat(day[:10])
    limit = dt.date.fromisoformat(SESSIONS[-1])
    while probe <= limit:
        iso = probe.isoformat()
        if iso in SESSION_SET:
            return iso
        probe += dt.timedelta(days=1)
    return None


def main() -> int:
    print("== 1. version ids come from pit_store, never redeclared ==")
    check("latency version", P.LATENCY_POLICY_VERSION == "information_latency_policy_v1",
          P.LATENCY_POLICY_VERSION)
    check("ladder version", P.LADDER_VERSION == "concept_ladder_v1", P.LADDER_VERSION)
    check("same object as pit_store's",
          P.LATENCY_POLICY_VERSION is pit_store.LATENCY_POLICY_VERSION
          and P.LADDER_VERSION is pit_store.LADDER_VERSION)
    check("policy records carry their version",
          P.latency_policy()["version"] == pit_store.LATENCY_POLICY_VERSION
          and P.ladder_spec()["ladder_version"] == pit_store.LADDER_VERSION)
    check("the cutoff is 15:30, not 16:00",
          P.latency_policy()["cutoff_local_time"] == "15:30"
          and P.LATENCY_CUTOFF == dt.time(15, 30))

    print("== 2. the 15:30 boundary, both directions ==")
    # 2019-06-05 and 2019-06-06 are ordinary Wednesday/Thursday sessions.
    check("15:29 ET is eligible for that day's close",
          P.available_date("2019-06-05T15:29:00", "2019-06-05", next_session) == "2019-06-05")
    check("15:30:00 exactly is still eligible (at or before)",
          P.available_date("2019-06-05T15:30:00", "2019-06-05", next_session) == "2019-06-05")
    check("15:30:01 is already late",
          P.available_date("2019-06-05T15:30:01", "2019-06-05", next_session) == "2019-06-06")
    check("15:31 ET waits for the next session",
          P.available_date("2019-06-05T15:31:00", "2019-06-05", next_session) == "2019-06-06")
    check("16:05 ET -- the 65.9% case -- waits",
          P.available_date("2019-06-05T16:05:30", "2019-06-05", next_session) == "2019-06-06")
    # A LATE-EVENING filing. EDGAR accepts submissions until 22:00 ET, and the
    # branch has to roll the acceptance day forward by exactly one day -- not
    # to skip a session, and not to wrap past midnight into two.
    check("21:47 ET -- a late-evening filing -- waits one session",
          P.available_date("2019-06-05T21:47:00", "2019-06-05", next_session) == "2019-06-06")
    check("23:59:59 rolls forward by exactly one session, never two",
          P.available_date("2019-06-05T23:59:59", "2019-06-05", next_session) == "2019-06-06")
    check("a late-evening filing is the next-session branch, read as Eastern",
          P.available_date_detail("2019-06-05T21:47:00", "2019-06-05",
                                  next_session)["rule"] == P.RULE_NEXT_SESSION
          and P.available_date_detail("2019-06-05T21:47:00", "2019-06-05",
                                      next_session)["interpretation"]
          == P.ACCEPTED_NAIVE_AS_ET)
    # ...and its REAL shape. EDGAR gives anything accepted after 17:30 ET the
    # NEXT business day as its `filed` date, so a genuine late-evening filing
    # has acceptance day != filed, and the two branches must agree on one answer.
    evening = P.available_date_detail("2019-06-05T21:47:00", "2019-06-06", next_session)
    check("accepted 21:47 Wednesday, filed Thursday -> available Thursday",
          evening["available_date"] == "2019-06-06", evening["available_date"])
    check("...and it still never precedes `filed`",
          evening["available_date"] >= evening["filed"], evening)
    check("the rule is reported, not just the date",
          P.available_date_detail("2019-06-05T15:29:00", "2019-06-05", next_session)["rule"]
          == P.RULE_SAME_SESSION
          and P.available_date_detail("2019-06-05T15:31:00", "2019-06-05",
                                      next_session)["rule"] == P.RULE_NEXT_SESSION)

    print("== 3. a filing with no acceptance time is pessimistic ==")
    detail = P.available_date_detail(None, "2019-06-05", next_session)
    check("no timestamp -> the NEXT session, never same-day",
          detail["available_date"] == "2019-06-06", detail["available_date"])
    check("the reason is recorded", detail["rule"] == P.RULE_NO_ACCEPTANCE_TIME)
    check("an empty string is the same as None",
          P.available_date("", "2019-06-05", next_session) == "2019-06-06")
    check("unparseable junk is treated as unknown, not as midnight",
          P.available_date_detail("not-a-time", "2019-06-05", next_session)["rule"]
          == P.RULE_NO_ACCEPTANCE_TIME)
    check("the interpretation says so",
          P.available_date_detail("not-a-time", "2019-06-05",
                                  next_session)["interpretation"] == P.ACCEPTED_UNPARSEABLE)

    print("== 4. weekends and holidays are skipped by session, not by day ==")
    # 2019-06-07 is a Friday; late on Friday means Monday the 10th.
    check("late Friday -> Monday",
          P.available_date("2019-06-07T16:05:00", "2019-06-07", next_session) == "2019-06-10")
    # 2019-07-03 is a Wednesday; the 4th is a holiday.
    check("late before a holiday -> the session after it",
          P.available_date("2019-07-03T17:00:00", "2019-07-03", next_session) == "2019-07-05")
    check("accepted ON a non-session resolves forward",
          P.available_date("2019-07-04T10:00:00", "2019-07-04", next_session) == "2019-07-05")
    # A WEEKEND acceptance. EDGAR's own window is business days, but DERA's
    # `accepted` column and a hand-entered timestamp can both carry a Saturday,
    # and NEITHER branch may hand back a day the market never opened. The 8th
    # and 9th of June 2019 are a Saturday and a Sunday; the 10th is a Monday.
    check("accepted early on a Saturday -> the following Monday session",
          P.available_date("2019-06-08T10:00:00", "2019-06-08", next_session) == "2019-06-10")
    check("accepted late on a Saturday -> still the following Monday, not Tuesday",
          P.available_date("2019-06-08T20:15:00", "2019-06-08", next_session) == "2019-06-10")
    check("accepted on a Sunday -> the following Monday",
          P.available_date("2019-06-09T11:00:00", "2019-06-09", next_session) == "2019-06-10")
    check("a weekend acceptance still reports which branch decided it",
          P.available_date_detail("2019-06-08T10:00:00", "2019-06-08",
                                  next_session)["rule"] == P.RULE_SAME_SESSION
          and P.available_date_detail("2019-06-08T20:15:00", "2019-06-08",
                                      next_session)["rule"] == P.RULE_NEXT_SESSION)
    check("a weekend availability date is resolved from the calendar, not guessed",
          P.available_date_detail("2019-06-08T10:00:00", "2019-06-08",
                                  next_session)["session_resolved"] is True)
    check("EDGAR's real weekend shape -- accepted Saturday, filed Monday",
          P.available_date("2019-06-08T10:00:00", "2019-06-10", next_session) == "2019-06-10")
    check("a weekend filing with no timestamp is still pessimistic",
          P.available_date(None, "2019-06-07", next_session) == "2019-06-10")
    check("with no calendar the date is the plain calendar day, and flagged",
          P.available_date_detail("2019-06-07T16:05:00", "2019-06-07")["available_date"]
          == "2019-06-08"
          and P.available_date_detail("2019-06-07T16:05:00",
                                      "2019-06-07")["session_resolved"] is False)

    print("== 5. how an acceptance timestamp is read ==")
    check("naive is read as Eastern wall clock",
          P.available_date_detail("2019-06-05T15:29:00", "2019-06-05",
                                  next_session)["interpretation"] == P.ACCEPTED_NAIVE_AS_ET)
    check("EDGAR's Z is ignored: 16:05Z is a 16:05 ET filing, so it waits",
          P.available_date("2019-06-05T16:05:30.000Z", "2019-06-05", next_session)
          == "2019-06-06")
    check("...and the reading is recorded as such",
          P.available_date_detail("2019-06-05T16:05:30.000Z", "2019-06-05",
                                  next_session)["interpretation"] == P.ACCEPTED_UTC_AS_ET)
    check("a real non-UTC offset is converted: 21:05+02:00 is 15:05 ET, eligible",
          P.available_date("2019-06-05T21:05:00+02:00", "2019-06-05", next_session)
          == "2019-06-05")
    check("...and that one says it was converted",
          P.available_date_detail("2019-06-05T21:05:00+02:00", "2019-06-05",
                                  next_session)["interpretation"]
          == P.ACCEPTED_OFFSET_CONVERTED)
    check("a DERA-style space separator parses",
          P.available_date("2019-06-05 15:29:00", "2019-06-05", next_session) == "2019-06-05")
    check("a naive datetime object is read as Eastern too",
          P.available_date(dt.datetime(2019, 6, 5, 16, 5), "2019-06-05", next_session)
          == "2019-06-06")
    check("an aware datetime object is converted",
          P.available_date(
              dt.datetime(2019, 6, 5, 21, 5,
                          tzinfo=dt.timezone(dt.timedelta(hours=2))),
              "2019-06-05", next_session) == "2019-06-05")

    print("== 6. the result never violates pit_fact's CHECK ==")
    # EDGAR gives a filing accepted after 17:30 the NEXT business day as its
    # filing date: accepted 18:00 Friday, filed Monday.
    late = P.available_date_detail("2019-06-07T18:00:00", "2019-06-10", next_session)
    check("available_date >= filed for a post-17:30 Friday acceptance",
          late["available_date"] >= "2019-06-10", late["available_date"])
    check("and it is the Monday itself, not the Tuesday",
          late["available_date"] == "2019-06-10", late["available_date"])
    sample = [("2019-06-05T09:00:00", "2019-06-05"), ("2019-06-05T16:05:00", "2019-06-05"),
              (None, "2019-06-05"), ("2019-07-03T17:00:00", "2019-07-03"),
              ("2019-06-07T18:00:00", "2019-06-10"), ("", "2019-12-31")]
    check("available_date >= filed holds for every sample",
          all(P.available_date(a, f, next_session) >= f for a, f in sample))

    print("== 7. the ASC 606 date gate ==")
    rev_2016 = [r.key for r in P.resolve("revenue", "2016-06-30")]
    rev_2019 = [r.key for r in P.resolve("revenue", "2019-06-30")]
    check("the ASC 606 tag is INVISIBLE at a 2016 as-of",
          "RevenueFromContractWithCustomerExcludingAssessedTax" not in rev_2016, rev_2016)
    check("the gross ASC 606 tag is invisible too",
          "RevenueFromContractWithCustomerIncludingAssessedTax" not in rev_2016)
    check("2016 revenue starts at Revenues", rev_2016[0] == "Revenues", rev_2016)
    check("2016 keeps the four pre-606 rungs", len(rev_2016) == 4, rev_2016)
    check("the ASC 606 tag IS present at a 2019 as-of",
          "RevenueFromContractWithCustomerExcludingAssessedTax" in rev_2019, rev_2019)
    check("and it leads the ladder from 2018",
          rev_2019[0] == "RevenueFromContractWithCustomerExcludingAssessedTax")
    check("2019 has all six rungs", len(rev_2019) == 6, rev_2019)
    check("the gate opens on 2018-01-01 exactly",
          "RevenueFromContractWithCustomerExcludingAssessedTax"
          in [r.key for r in P.resolve("revenue", "2018-01-01")]
          and "RevenueFromContractWithCustomerExcludingAssessedTax"
          not in [r.key for r in P.resolve("revenue", "2017-12-31")])
    check("the gate is a field on the rung, not a comment",
          P.ladder_for("revenue").rungs[0].min_filed_date == "2018-01-01")
    check("ungated rungs carry no gate",
          P.ladder_for("revenue").rungs[1].min_filed_date is None)
    check("relative order is preserved after gating",
          rev_2016 == [k for k in rev_2019 if not k.startswith("RevenueFromContract")])

    print("== 8. a composite rung resolves ==")
    debt = P.resolve("total_debt", "2019-06-30")
    first = debt[0]
    check("total_debt leads with a composite rung", first.combine == P.COMBINE_SUM)
    check("it sums three tags",
          first.tags == ("LongTermDebtNoncurrent", "LongTermDebtCurrent",
                         "ShortTermBorrowings"), first.tags)
    check("its source_tag key names the whole sum",
          first.key == "SUM(LongTermDebtNoncurrent+LongTermDebtCurrent+ShortTermBorrowings)",
          first.key)
    check("asking a composite rung for a single tag raises",
          _raises(lambda: first.tag, ValueError))
    check("the fallbacks are single tags",
          [r.key for r in debt[1:]] == ["LongTermDebt",
                                        "LongTermDebtAndCapitalLeaseObligations",
                                        "DebtLongtermAndShorttermCombinedAmount"])
    check("a single rung still answers .tag",
          P.resolve("total_assets", "2019-06-30")[0].tag == "Assets")
    check("the 56% structural hole is recorded",
          P.ladder_for("total_debt").coverage_cy2019 == 3459)

    print("== 9. every required concept is defined ==")
    required = ("revenue", "net_income", "operating_income",
                "depreciation_amortisation", "cash", "total_debt", "total_assets",
                "equity", "operating_cash_flow", "capex", "shares_outstanding",
                "interest_expense", "gross_profit")
    for concept in required:
        check(f"ladder {concept}", concept in P.concepts())
    check("net_income falls back to ProfitLoss -- the SVB rung",
          [r.key for r in P.resolve("net_income", "2019-06-30")][:2]
          == ["NetIncomeLoss", "ProfitLoss"])
    check("operating_income is the binding EBITDA constraint at 5,234",
          P.ladder_for("operating_income").coverage_cy2019 == 5234)
    check("D&A union coverage recorded at 6,144",
          P.ladder_for("depreciation_amortisation").coverage_cy2019 == 6144)
    check("the dei cover-page share count leads shares_outstanding",
          P.resolve("shares_outstanding", "2019-06-30")[0].taxonomy == P.TAXONOMY_DEI)
    check("unmeasured ladders are flagged as unmeasured",
          P.ladder_for("interest_expense").measured is False
          and P.ladder_for("gross_profit").measured is False)
    check("measured ladders are flagged measured",
          all(P.ladder_for(c).measured for c in
              ("revenue", "net_income", "operating_income", "total_debt")))
    check("an unknown concept raises rather than returning an empty ladder",
          _raises(lambda: P.resolve("free_cash_flow", "2019-06-30"), ValueError))
    check("balance-sheet concepts are instantaneous",
          all(P.ladder_for(c).period_kind == P.PERIOD_INSTANT
              for c in ("cash", "total_debt", "total_assets", "equity",
                        "shares_outstanding")))
    check("income and cash-flow concepts are durations",
          all(P.ladder_for(c).period_kind == P.PERIOD_DURATION
              for c in ("revenue", "net_income", "operating_income",
                        "depreciation_amortisation", "operating_cash_flow", "capex")))

    print("== 10. staleness at the boundary ==")
    check("annual budget is 15 months", P.MAX_AGE_ANNUAL_MONTHS == 15)
    check("quarterly budget is 6 months", P.MAX_AGE_QUARTERLY_MONTHS == 6)
    check("FY2019 is usable on the last day of the 15th month",
          not P.is_stale("2019-12-31", "2021-03-31", 4))
    check("FY2019 is stale the very next day",
          P.is_stale("2019-12-31", "2021-04-01", 4))
    check("Q3 2019 is usable at exactly 6 months",
          not P.is_stale("2019-09-30", "2020-03-30", 1))
    check("Q3 2019 is stale at 6 months and a day",
          P.is_stale("2019-09-30", "2020-03-31", 1))
    check("an instantaneous fact (qtrs 0) gets the quarterly budget",
          P.max_age_months(0) == P.MAX_AGE_QUARTERLY_MONTHS
          and P.is_stale("2019-09-30", "2020-03-31", 0))
    check("a YTD 3-quarter fact gets the quarterly budget",
          P.max_age_months(3) == P.MAX_AGE_QUARTERLY_MONTHS)
    check("the SVB failure: FY2011 net income at a 2019 as-of is stale",
          P.is_stale("2011-12-31", "2019-06-30", 4))
    check("month arithmetic clamps a short month",
          not P.is_stale("2019-11-30", "2021-02-28", 4)
          and P.is_stale("2019-11-30", "2021-03-01", 4))
    check("the shares_outstanding override is tighter",
          P.max_age_months(4, "shares_outstanding") == 12
          and P.max_age_months(1, "shares_outstanding") == 4)
    check("...and it bites where the default would not",
          P.is_stale("2019-12-31", "2021-02-01", 4, "shares_outstanding")
          and not P.is_stale("2019-12-31", "2021-02-01", 4))
    check("an unparseable period end is stale, never usable",
          P.is_stale("", "2019-06-30", 4) and P.is_stale(None, "2019-06-30", 1))
    check("the day bound is never TIGHTER than the month rule",
          P.max_age_days(4) >= 458 and P.max_age_days(1) >= 184,
          (P.max_age_days(4), P.max_age_days(1)))
    check("the day bound respects the concept override",
          P.max_age_days(4, "shares_outstanding") < P.max_age_days(4))

    print("== 11. the EBITDA assembly spec is declarative ==")
    spec = P.ebitda_assembly_spec()
    check("it is operating income plus D&A",
          [c["concept"] for c in spec["components"]]
          == ["operating_income", "depreciation_amortisation"])
    check("both components are required", all(c["required"] for c in spec["components"]))
    check("same filing preferred", spec["same_filing_preferred"] is True)
    check("a mixed-accession flag is named",
          spec["mixed_accession_flag"] == P.EBITDA_MIXED_ACCESSION_FLAG)
    check("it says WHY sources_json is an array",
          "array" in spec["why_sources_json_is_an_array"].lower())
    check("availability is the LAST component's",
          "MAX" in spec["availability_rule"])
    check("missing EBITDA is unavailable, never imputed",
          spec["availability_on_missing"]["availability"] == "unavailable"
          and spec["availability_on_missing"]["reason"] == pit_store.REASON_LADDER_EXHAUSTED)
    check("two accessions are mixed", P.mixed_accession(["0000320193-19-1", "0000320193-19-2"]))
    check("one accession is not", not P.mixed_accession(["0000320193-19-1"] * 2))
    check("a missing accession counts as its own source",
          P.mixed_accession(["0000320193-19-1", None]))
    check("the spec is a copy, so a caller cannot mutate the policy",
          (spec["components"].clear() or True)
          and len(P.ebitda_assembly_spec()["components"]) == 2)

    print("== 12. everything round-trips through json ==")
    encoded = json.dumps(P.ladder_spec(), sort_keys=True)
    decoded = json.loads(encoded)
    check("ladder_spec() survives json.dumps", isinstance(encoded, str) and len(encoded) > 500)
    check("round-trip is stable",
          json.dumps(decoded, sort_keys=True) == encoded)
    check("the gate survives the round trip",
          decoded["concepts"]["revenue"]["rungs"][0]["min_filed_date"] == "2018-01-01")
    check("the composite rung survives the round trip",
          decoded["concepts"]["total_debt"]["rungs"][0]["tags"]
          == ["LongTermDebtNoncurrent", "LongTermDebtCurrent", "ShortTermBorrowings"]
          and decoded["concepts"]["total_debt"]["rungs"][0]["combine"] == "sum")
    check("rung order survives the round trip",
          [r["key"] for r in decoded["concepts"]["net_income"]["rungs"]]
          == ["NetIncomeLoss", "ProfitLoss",
              "NetIncomeLossAvailableToCommonStockholdersBasic"])
    check("every concept is in the spec",
          set(decoded["concepts"]) == set(required))
    bundle = json.dumps(P.policy_bundle(), sort_keys=True)
    check("the whole policy bundle serialises for a replay run",
          isinstance(bundle, str)
          and set(json.loads(bundle)) == {"latency", "ladders", "staleness", "ebitda"})
    check("the bundle records both version ids",
          json.loads(bundle)["latency"]["version"] == pit_store.LATENCY_POLICY_VERSION
          and json.loads(bundle)["staleness"]["ladder_version"] == pit_store.LADDER_VERSION)

    # ----------------------------------------------------------------------
    # v1 IS FROZEN. This is the section that makes the versioning real rather
    # than decorative: if v1 ever changes, every row stamped concept_ladder_v1
    # stops being reproducible, and no coverage number measured under it can be
    # compared with anything. The digest is of the serialised spec, because
    # that is exactly what pit_feature_definition.tag_ladder_json stores.
    # ----------------------------------------------------------------------
    print("== 13. concept_ladder_v1 is byte-frozen ==")
    v1_spec = json.dumps(P.ladder_spec(), sort_keys=True)
    check("v1 ladder_spec() is 13,039 bytes", len(v1_spec) == V1_SPEC_BYTES,
          len(v1_spec))
    check("v1 ladder_spec() digest is unchanged",
          hashlib.sha256(v1_spec.encode("utf-8")).hexdigest() == V1_SPEC_SHA256,
          hashlib.sha256(v1_spec.encode("utf-8")).hexdigest())
    check("the default bundle's digest is unchanged",
          hashlib.sha256(json.dumps(P.policy_bundle(),
                                    sort_keys=True).encode("utf-8")).hexdigest()
          == V1_BUNDLE_SHA256)
    check("ladder_spec() with no version IS v1",
          v1_spec == json.dumps(P.ladder_spec(pit_store.LADDER_VERSION), sort_keys=True))
    check("v1 total_debt still has exactly four rungs",
          len(P.ladder_for("total_debt").rungs) == 4)
    check("v1 total_debt rung order is untouched",
          [r.key for r in P.ladder_for("total_debt").rungs]
          == ["SUM(LongTermDebtNoncurrent+LongTermDebtCurrent+ShortTermBorrowings)",
              "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations",
              "DebtLongtermAndShorttermCombinedAmount"])
    check("no v1 rung carries a quantity label -- v1 could not tell total debt "
          "from long-term debt, and it must not pretend otherwise now",
          all("quantity" not in rung and "bound" not in rung
              for c in P.ladder_spec()["concepts"].values()
              for rung in c["rungs"]))
    check("no v1 ladder carries a consumer rule either",
          all("consumer_rule" not in c
              for c in P.ladder_spec()["concepts"].values()))
    check("resolve() with no version resolves exactly as v1",
          all([r.key for r in P.resolve(c, "2019-06-28")]
              == [r.key for r in P.resolve(c, "2019-06-28", pit_store.LADDER_VERSION)]
              for c in P.concepts()))
    check("the default version is v1, not the newest",
          P.DEFAULT_LADDER_VERSION == pit_store.LADDER_VERSION)

    print("== 14. concept_ladder_v2 reaches the rungs v1 trapped ==")
    V2 = pit_store.LADDER_VERSION_V2
    check("v2 has its own id", V2 == "concept_ladder_v2" and V2 != P.LADDER_VERSION)
    check("both versions are selectable",
          P.ladder_versions() == (pit_store.LADDER_VERSION, V2))
    check("an unknown version raises rather than falling back",
          _raises(lambda: P.ladder_for("total_debt", "concept_ladder_v9"), ValueError))
    v2_debt = P.resolve("total_debt", "2019-06-28", V2)
    keys = [r.key for r in v2_debt]
    check("v2 total_debt has six rungs", len(v2_debt) == 6, keys)
    check("LongTermDebtNoncurrent is reachable ALONE -- the whole point",
          "LongTermDebtNoncurrent" in keys, keys)
    check("...and it is UNREACHABLE alone under v1",
          "LongTermDebtNoncurrent"
          not in [r.key for r in P.resolve("total_debt", "2019-06-28")])
    check("the two-component sum is reachable",
          "SUM(LongTermDebtNoncurrent+LongTermDebtCurrent)" in keys, keys)
    check("v2 still leads with the only rung that is actually total debt",
          keys[0] == "SUM(LongTermDebtNoncurrent+LongTermDebtCurrent+"
                     "ShortTermBorrowings)")
    check("every v2 total_debt rung declares an economic quantity",
          all(r.quantity for r in v2_debt))
    check("the exact rungs come before every lower bound -- the ladder is "
          "ordered by completeness, never by coverage",
          [r.bound for r in v2_debt]
          == [P.BOUND_EXACT, P.BOUND_EXACT] + [P.BOUND_LOWER] * 4,
          [r.bound for r in v2_debt])
    completeness = [P.DEBT_Q_TOTAL, P.DEBT_Q_LONG_TERM_ALL,
                    P.DEBT_Q_LONG_TERM_WITH_LEASES, P.DEBT_Q_LONG_TERM_NONCURRENT]
    check("the noncurrent-only rung is LAST, because it is least complete",
          v2_debt[-1].quantity == P.DEBT_Q_LONG_TERM_NONCURRENT)
    check("every declared quantity is one of the four named constants",
          all(r.quantity in completeness for r in v2_debt))
    check("v2 introduces no tag the ingest did not retain -- no re-ingest",
          P.ladder_tags(V2) <= P.ladder_tags(pit_store.LADDER_VERSION))
    check("all_ladder_tags() is the union both versions need",
          P.all_ladder_tags() == P.ladder_tags() | P.ladder_tags(V2))
    check("v2 differs from v1 in EXACTLY one concept",
          [c for c in P.concepts(V2)
           if P.ladder_for(c, V2) is not P.ladder_for(c)] == ["total_debt"])
    check("the other concepts are the SAME objects, so they cannot drift",
          P.ladder_for("revenue", V2) is P.ladder_for("revenue"))
    check("v2 carries the consumer rule with the ladder, not in a docstring",
          "source_tag_changed" in P.ladder_for("total_debt", V2).consumer_rule
          and "lower_bound" in P.ladder_for("total_debt", V2).consumer_rule)
    check("v1's ladders carry no consumer rule",
          P.ladder_for("total_debt").consumer_rule == "")
    check("the rule survives into the stored spec",
          "consumer_rule" in P.ladder_spec(V2)["concepts"]["total_debt"])
    check("v2's spec is stamped v2", P.ladder_spec(V2)["ladder_version"] == V2)
    check("a v2 bundle restamps every record that names a ladder version",
          P.policy_bundle(V2)["ladders"]["ladder_version"] == V2
          and P.policy_bundle(V2)["staleness"]["ladder_version"] == V2
          and P.policy_bundle(V2)["ebitda"]["ladder_version"] == V2)
    check("a v2 bundle still round-trips through json",
          isinstance(json.dumps(P.policy_bundle(V2), sort_keys=True), str))
    check("v2 keeps the balance-sheet period kind and the USD unit",
          P.ladder_for("total_debt", V2).period_kind == P.PERIOD_INSTANT
          and P.ladder_for("total_debt", V2).unit == P.UNIT_USD)
    check("v2 quotes no CY2019 coverage number it has not measured",
          P.ladder_for("total_debt", V2).coverage_cy2019 is None)
    check("the rejected fragments are not rungs: the current portion alone is "
          "not a bound on total debt",
          "LongTermDebtCurrent" not in keys and "ShortTermBorrowings" not in keys)

    print("== 15. a rung change is visible to the caller ==")
    lower = P.quantity_for("total_debt", "LongTermDebtNoncurrent", V2)
    upper = P.quantity_for("total_debt",
                           "SUM(LongTermDebtNoncurrent+LongTermDebtCurrent)", V2)
    check("a stored source_tag maps back to its quantity",
          lower["quantity"] == P.DEBT_Q_LONG_TERM_NONCURRENT and lower["known"])
    check("...and to its bound", lower["bound"] == P.BOUND_LOWER)
    check("two adjacent as-of rows on different rungs report DIFFERENT quantities "
          "-- which is how a consumer knows the balance sheet did not move",
          lower["quantity"] != upper["quantity"])
    check("the rung's position is recoverable too",
          upper["rung_index"] < lower["rung_index"])
    check("rung_for_key is the inverse of rung_key",
          all(P.rung_for_key("total_debt", P.rung_key(r), V2) is r for r in v2_debt))
    check("an unknown source_tag is reported unknown, never guessed",
          P.quantity_for("total_debt", "MadeUpTag", V2)["known"] is False
          and P.rung_for_key("total_debt", "MadeUpTag", V2) is None)
    check("a v1 row is reported as carrying NO quantity label",
          P.quantity_for("total_debt", "LongTermDebt")["known"] is False
          and P.quantity_for("total_debt", "LongTermDebt")["rung_index"] == 1)
    check("the same tag can sit at a different rung index in each version",
          P.quantity_for("total_debt", "LongTermDebtAndCapitalLeaseObligations",
                         V2)["rung_index"]
          != P.quantity_for("total_debt",
                            "LongTermDebtAndCapitalLeaseObligations")["rung_index"])
    changed = [(a, b, P.quantity_for("total_debt", a, V2)["quantity"]
                != P.quantity_for("total_debt", b, V2)["quantity"])
               for a, b in (("LongTermDebtNoncurrent", "LongTermDebtNoncurrent"),
                            ("LongTermDebtNoncurrent",
                             "SUM(LongTermDebtNoncurrent+LongTermDebtCurrent)"))]
    check("an unchanged rung is not flagged as a change", changed[0][2] is False)
    check("a changed rung is flagged", changed[1][2] is True)

    print("== 16. what no ladder version can repair is recorded, not implied ==")
    limits = P.absence_limits()
    check("the discarded-row count is on the record",
          limits["why_not_computable"]["rows_discarded_at_ingest"] == 159522)
    check("it names the three states it cannot separate",
          all(s in json.dumps(limits) for s in (pit_store.REASON_NEVER_TAGGED,
                                                pit_store.REASON_TAGGED_ZERO,
                                                pit_store.REASON_COMPANY_HAD_NONE)))
    check("it says what a repair would cost, in bytes and in schema",
          "5.26 GiB" in json.dumps(limits)
          and "pit_fact_absence" in json.dumps(limits["what_it_would_take"]["1_schema"]))
    check("it refuses the nullable-val shortcut for a stated reason",
          "NOT NULL" in limits["what_it_would_take"]["1_schema"])
    check("the dead D&A rung is recorded rather than quietly deleted",
          limits["dead_rung"]["rows_in_store"] == 0
          and limits["dead_rung"]["tag"] in
          [r.tag for r in P.resolve("depreciation_amortisation", "2019-06-28")])
    check("the record is a copy, so a caller cannot mutate the policy",
          (limits.clear() or True) and P.absence_limits()["measured_on"] == "2026-09-21")
    check("v2's consumer rule points at it instead of repeating it",
          "absence_limits()" in P.ladder_for("total_debt", V2).consumer_rule)

    print()
    print("FAILURES:", len(fails), fails if fails else "")
    return 1 if fails else 0


def _raises(thunk, exc) -> bool:
    try:
        thunk()
    except exc:
        return True
    except Exception:
        return False
    return False


if __name__ == "__main__":
    sys.exit(main())
