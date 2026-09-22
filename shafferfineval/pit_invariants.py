"""The economic specification layer: Shaffer's intent, written so it executes.

A comment saying "the EBITDA benchmark must not depend on price" does not stop
the EBITDA benchmark from depending on price. One did, for the whole life of
this project -- `build_ebitda_peer_cohort` filtered peers to those carrying a
usable EV/EBITDA before taking percentiles, which made a FUNDAMENTAL OPERATING
BENCHMARK conditional on price, shares, debt and cash. It survived a design
document, a review and a coverage study. It was caught only when the derivation
graph made the leaf set explicit and someone looked at the list.

So the rule this module enforces:

    RequiredLeaves(Factor) MUST EQUAL the minimal economically necessary leaf set

Minimality is two claims, and both are checkable:

    SUFFICIENCY   nothing outside the declared set is consulted
    NECESSITY     every leaf in the declared set is actually load-bearing

Equality of the declared set and the graph's de-duplicated actual set gives
both at once. That single equality catches two different bugs:

  * CONTAMINATION -- an accidental dependency. The EBITDA benchmark reaching
    for price. Sufficiency fails: a leaf appears that the economics never
    called for.
  * DOUBLE-CHARGING -- a duplicated intermediate. EBITDA acceleration is
    growth(t) minus growth(t-1), and those two share the t-1 observation.
    Composing them naively charges for it twice and reports 6.25% coverage
    where the honest figure is 12.5%. De-duplication is why the declared set
    is written over LEAVES rather than over intermediate features.

The second half of the module is the evidence protocol, which exists because a
row count is not a sample size. 355,727 twelve-month label rows are worth 160.7
independent observations once overlap and cross-sectional correlation are taken
out, and a metric computed on 355,727 rows while quoting the precision of
355,727 draws is the most flattering mistake available in this domain. Status
here depends on EFFECTIVE observations and folds, never on rows -- and a cell
too small to support a claim is INSUFFICIENT_EVIDENCE, which is a third answer
distinct from good and bad.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence

import pit_derive

SPEC_VERSION = "economic_spec_v1"

_NL = chr(10)

# --------------------------------------------------------------------------
# The families an invariant can forbid or require
# --------------------------------------------------------------------------

#: Anything whose availability depends on a market price existing for the name.
#: A fundamental operating measure that touches one of these has stopped being
#: a fundamental operating measure, whatever its docstring says.
PRICE_DEPENDENT_LEAVES: frozenset[str] = frozenset({
    "price_raw", "price_adjusted", "shares_outstanding",
    "market_cap", "enterprise_value",
})

#: Facts that come from a filing rather than a market.
FUNDAMENTAL_LEAVES: frozenset[str] = frozenset({
    "revenue", "operating_income", "depreciation_amortisation", "net_income",
    "total_debt", "cash", "total_assets", "equity", "operating_cash_flow",
    "capex", "interest_expense",
})

#: Facts that come from outside the company entirely.
EXOGENOUS_LEAVES: frozenset[str] = frozenset({"cpi", "treasury_yield", "sic"})


# --------------------------------------------------------------------------
# An invariant
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Invariant:
    """One executable economic claim about one factor.

    `claim` is the sentence a human would argue with. `exact_leaves`, when
    given, is the strongest form: the factor's de-duplicated leaf set must
    equal this, no more and no less. `forbidden` and `required` are the weaker
    forms for factors whose leaf set legitimately varies (a ladder with
    fallback rungs, say) but whose boundaries are still fixed.

    `lag_insensitive` compares leaf KEYS rather than leaf ids, for a claim
    about WHICH facts are needed rather than at how many dates.
    """

    factor: str
    claim: str
    exact_leaves: Optional[frozenset[str]] = None
    forbidden: frozenset[str] = frozenset()
    required: frozenset[str] = frozenset()
    lag_insensitive: bool = False
    note: str = ""


@dataclass(frozen=True)
class InvariantResult:
    factor: str
    claim: str
    ok: bool
    detail: str
    actual_leaves: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()


# --------------------------------------------------------------------------
# The declared specification
# --------------------------------------------------------------------------

INVARIANTS: tuple[Invariant, ...] = (

    Invariant(
        factor="ebitda_benchmark_gap",
        claim=("The EBITDA benchmark is a FUNDAMENTAL OPERATING comparison and "
               "must not be conditional on a market price, a share count, a "
               "debt figure or a cash balance."),
        forbidden=PRICE_DEPENDENT_LEAVES,
        note=("This is the invariant that would have caught the contamination. "
              "The 50-75 cohort needs EBITDA; eligibility for it is an EBITDA "
              "question and nothing else."),
    ),
    Invariant(
        factor="cohort_mean_ebitda",
        claim="The 50-75 cohort mean is built from peers with valid EBITDA, full stop.",
        forbidden=PRICE_DEPENDENT_LEAVES,
    ),
    Invariant(
        factor="sector_ebitda_p50",
        claim=("The sector EBITDA percentiles are the SECTOR's percentiles, not "
               "the PRICED sector's. A size statistic selected on having a market "
               "price is a size statistic selected on size."),
        forbidden=PRICE_DEPENDENT_LEAVES,
    ),
    Invariant(
        factor="sector_ebitda_p75",
        claim="As p50.",
        forbidden=PRICE_DEPENDENT_LEAVES,
    ),
    Invariant(
        factor="ebitda_scale",
        claim="Scale is an EBITDA question. It does not need a price.",
        forbidden=PRICE_DEPENDENT_LEAVES,
    ),
    Invariant(
        factor="ebitda_margin",
        claim="Efficiency needs EBITDA and revenue, and nothing else.",
        exact_leaves=frozenset({"operating_income", "depreciation_amortisation",
                                "revenue"}),
        lag_insensitive=True,
    ),
    Invariant(
        factor="real_revenue_growth",
        claim=("Real growth is revenue at two dates deflated by contemporaneous "
               "inflation. It does not need a price, an earnings figure or EBITDA."),
        exact_leaves=frozenset({"revenue", "cpi"}),
        lag_insensitive=True,
        note=("Written lag-insensitively on purpose: the claim is about WHICH "
              "facts, and the lags are checked separately by the minimality test."),
    ),
    Invariant(
        factor="ebitda_acceleration",
        claim=("Acceleration needs EBITDA at three dates -- t, t-1, t-2 -- and "
               "therefore SIX primitive leaves, not eight. The two growth terms "
               "share the t-1 observation and it must be charged once."),
        exact_leaves=frozenset({
            "operating_income", "operating_income@t-1y", "operating_income@t-2y",
            "depreciation_amortisation", "depreciation_amortisation@t-1y",
            "depreciation_amortisation@t-2y",
        }),
    ),
    Invariant(
        factor="ev_ebitda",
        claim=("EV/EBITDA legitimately needs the hardest chain in the model: "
               "price, shares, debt, cash and EBITDA. This invariant exists to "
               "record that the cost is INTENDED here, unlike the benchmark."),
        required=frozenset({"total_debt", "cash"}),
        note=("The contrast with ebitda_benchmark_gap is the point. The same "
              "leaves that are contamination there are the specification here."),
    ),
    Invariant(
        factor="pe_ratio",
        claim=("P/E is the broad valuation backbone precisely because it routes "
               "around the binding leaf: price over per-share earnings needs no "
               "share count, no debt figure and no cash balance."),
        forbidden=frozenset({"total_debt", "cash"}),
        note=("If this invariant ever fails, P/E has quietly become EV/EBITDA "
              "and the valuation pillar has lost its broad member."),
    ),
    Invariant(
        factor="interest_coverage",
        claim="Coverage is an operating-versus-interest question; no market data.",
        forbidden=PRICE_DEPENDENT_LEAVES,
    ),
    Invariant(
        factor="roa",
        claim="Return on assets is a filing-only measure.",
        forbidden=PRICE_DEPENDENT_LEAVES,
    ),
    Invariant(
        factor="fcf_conversion",
        claim="Cash conversion is operating cash flow, capex and EBITDA. No price.",
        forbidden=PRICE_DEPENDENT_LEAVES,
    ),
    Invariant(
        factor="net_debt_ebitda",
        claim="Leverage against earnings needs debt, cash and EBITDA. No price.",
        forbidden=PRICE_DEPENDENT_LEAVES,
        required=frozenset({"total_debt", "cash"}),
    ),
)


# --------------------------------------------------------------------------
# Checking
# --------------------------------------------------------------------------

def actual_leaves(factor: str, *, lag_insensitive: bool = False) -> tuple[str, ...]:
    """The de-duplicated primitive leaf set the graph says this factor needs."""
    refs = pit_derive.leaves(factor)
    if lag_insensitive:
        return tuple(sorted({r.key for r in refs}))
    return tuple(sorted({r.leaf_id for r in refs}))


def check(inv: Invariant) -> InvariantResult:
    """Evaluate one economic claim against the derivation graph."""
    try:
        found = actual_leaves(inv.factor, lag_insensitive=inv.lag_insensitive)
    except KeyError:
        return InvariantResult(inv.factor, inv.claim, False,
                               f"no node named {inv.factor!r} in the graph")

    bare = {leaf.split("@")[0].split("[")[0] for leaf in found}
    violations: list[str] = []

    hit = sorted(bare & inv.forbidden)
    if hit:
        violations.append(
            f"FORBIDDEN leaves present: {', '.join(hit)} -- this factor has "
            f"become conditional on data its economics never called for")

    missing = sorted(inv.required - bare)
    if missing:
        violations.append(f"REQUIRED leaves absent: {', '.join(missing)}")

    if inv.exact_leaves is not None:
        declared = set(inv.exact_leaves)
        actual = set(found)
        extra = sorted(actual - declared)
        absent = sorted(declared - actual)
        if extra:
            violations.append(
                f"NOT MINIMAL -- {len(extra)} leaf(s) beyond the declared set: "
                f"{', '.join(extra)}. Either the economics changed or a "
                f"dependency crept in.")
        if absent:
            violations.append(
                f"NOT NECESSARY -- {len(absent)} declared leaf(s) the graph does "
                f"not use: {', '.join(absent)}. The declaration overstates what "
                f"the factor needs, which makes its coverage look worse than it is.")

    ok = not violations
    detail = ("minimal and clean" if ok else "; ".join(violations))
    return InvariantResult(inv.factor, inv.claim, ok, detail, found,
                           tuple(violations))


def check_all(invariants: Sequence[Invariant] = INVARIANTS) -> list[InvariantResult]:
    return [check(inv) for inv in invariants]


def minimality(factor: str) -> tuple[bool, str]:
    """Is this factor's leaf set free of duplicated intermediate requirements?

    The graph's `leaves()` already de-duplicates, so the test is whether the
    naive composition -- multiplying up through intermediate features -- would
    have charged for more leaves than exist. Where it would, the factor is one
    the coverage model can get badly wrong, and this names it.
    """
    node = pit_derive.node(factor)
    deduped = pit_derive.leaves(factor)
    naive = 0
    for edge in node.inputs:
        if not edge.required:
            continue
        try:
            naive += len(pit_derive.leaves(edge.key))
        except KeyError:
            naive += 1
    if naive <= len(deduped):
        return True, f"{len(deduped)} leaves; no shared intermediates"
    return False, (
        f"{len(deduped)} unique leaves but naive composition would charge for "
        f"{naive} -- {naive - len(deduped)} shared observation(s). Coverage MUST "
        f"be computed over the de-duplicated set.")


# --------------------------------------------------------------------------
# The evidence protocol
# --------------------------------------------------------------------------

STATUS_SUFFICIENT = "SUFFICIENT"
STATUS_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"

#: A fold that cannot support a metric should not report one. This is a floor
#: on EFFECTIVE observations, never on rows.
MIN_EFFECTIVE_PER_FOLD = 100
MIN_FOLDS = 3


def detectable_rho(n_effective: float, confidence_z: float = 1.96) -> float:
    """The smallest rank correlation distinguishable from zero at `n_effective`.

    SE(rho) ~= 1/sqrt(n-1), so the detectable effect is z/sqrt(n-1). This is the
    number that decides whether a horizon can be studied at all, and it is
    brutal at annual horizon: the store holds 160.7 independent 12M
    observations, which detects nothing below rho ~= 0.155 -- while realistic
    equity factor information coefficients live between 0.02 and 0.06.

    Quoting a 12M rank IC of 0.04 from this store would therefore be quoting
    noise with a decimal point on it.
    """
    if n_effective is None or n_effective <= 2:
        return float("inf")
    return confidence_z / math.sqrt(n_effective - 1.0)


@dataclass(frozen=True)
class EvidenceVerdict:
    status: str
    n_effective: float
    n_folds: int
    detectable: float
    reason: str

    @property
    def sufficient(self) -> bool:
        return self.status == STATUS_SUFFICIENT


def evidence_status(n_effective: float, n_folds: int,
                    target_effect: float = 0.05,
                    min_effective_per_fold: int = MIN_EFFECTIVE_PER_FOLD,
                    min_folds: int = MIN_FOLDS) -> EvidenceVerdict:
    """SUFFICIENT or INSUFFICIENT_EVIDENCE -- never good or bad.

    `target_effect` is the smallest rank correlation that would be economically
    interesting. If the cell cannot distinguish that from zero, it cannot
    support a claim in EITHER direction, and reporting it as a weak result
    would be reporting a measurement the data cannot make.
    """
    reasons: list[str] = []
    if n_folds < min_folds:
        reasons.append(f"{n_folds} folds, below the floor of {min_folds}")
    if n_folds > 0 and (n_effective / n_folds) < min_effective_per_fold:
        reasons.append(
            f"{n_effective / n_folds:.0f} effective observations per fold, "
            f"below the floor of {min_effective_per_fold}")
    detect = detectable_rho(n_effective)
    if detect > target_effect:
        reasons.append(
            f"detects nothing below rho {detect:.3f}, while the effect of "
            f"interest is {target_effect:.3f}")
    if reasons:
        return EvidenceVerdict(STATUS_INSUFFICIENT, n_effective, n_folds,
                               detect, "; ".join(reasons))
    return EvidenceVerdict(STATUS_SUFFICIENT, n_effective, n_folds, detect,
                           f"detects effects down to rho {detect:.3f}")


# --------------------------------------------------------------------------
# Attribution categories — a PARTITION of the leaf set
# --------------------------------------------------------------------------

CATEGORY_COMPANY = "company"      # primitives the issuer itself reports
CATEGORY_MARKET = "market"        # primitives set by a market price
CATEGORY_PEER = "peer"            # the benchmark / percentile context
CATEGORY_MACRO = "macro"          # primitives from outside the company entirely

ATTRIBUTION_CATEGORIES = (CATEGORY_COMPANY, CATEGORY_MARKET,
                          CATEGORY_PEER, CATEGORY_MACRO)

#: OWNERSHIP -- who the fact belongs to. A property of the PRIMITIVE, fixed
#: once and independent of which factor is consuming it.
#:
#: shares_outstanding is COMPANY, not MARKET, and the split matters in the UI:
#:      MarketCap = Price x Shares
#: price is market behaviour, shares are an issuer-controlled state variable.
#: So a buyback reads as a company effect, an issuance as a company effect, and
#: a re-rating as a market effect. Merging them would lose the ability to tell
#: "they bought back stock" from "the stock went up".
PRIMITIVE_OWNER: dict[str, str] = {
    # facts the issuer reports about itself
    "revenue": CATEGORY_COMPANY,
    "operating_income": CATEGORY_COMPANY,
    "depreciation_amortisation": CATEGORY_COMPANY,
    "net_income": CATEGORY_COMPANY,
    "total_debt": CATEGORY_COMPANY,
    "cash": CATEGORY_COMPANY,
    "total_assets": CATEGORY_COMPANY,
    "equity": CATEGORY_COMPANY,
    "operating_cash_flow": CATEGORY_COMPANY,
    "capex": CATEGORY_COMPANY,
    "interest_expense": CATEGORY_COMPANY,
    "shares_outstanding": CATEGORY_COMPANY,
    # set by a market, not by the issuer
    "price_raw": CATEGORY_MARKET,
    "price_adjusted": CATEGORY_MARKET,
    # outside any single company
    "cpi": CATEGORY_MACRO,
    "treasury_yield": CATEGORY_MACRO,
    "sic": CATEGORY_PEER,
}

#: Kept as an alias so existing callers do not break. OWNERSHIP is the concept.
LEAF_CATEGORY = PRIMITIVE_OWNER


def role_in_factor(factor: str, ref) -> str:
    """The ROLE a leaf plays in THIS factor, which is not its ownership.

    The distinction the owner drew, and the reason it generalises:

        shares_outstanding        ownership COMPANY
        peer_member ev_ebitda     role      PEER

    The same raw fact can be a company fact by ownership and peer context by
    role. Sector debt, sector margin and peer P/E are all filing facts owned by
    SOME company -- but when they enter THIS asset's factor they are defining
    the comparison set, and their role here is peer context.

    THE RULE: a leaf reached through a per-member (cohort) edge has role PEER,
    whatever it is owned by. Otherwise role equals ownership.

    Without this, attribution would report "Microsoft's fundamentals improved"
    when what actually happened is that Microsoft's peers deteriorated.
    """
    if getattr(ref, "per_member", False):
        return CATEGORY_PEER
    return PRIMITIVE_OWNER.get(getattr(ref, "key", ref), "UNCATEGORISED")


def categorise_leaves(factor: str) -> dict[str, tuple[str, ...]]:
    """Group a factor's unique leaves by their ROLE in that factor.

    Roles, not ownerships: see `role_in_factor`. The Shapley attribution runs
    over these groups, so a mis-assignment here produces a decomposition that
    reconciles perfectly and means the wrong thing.
    """
    out: dict[str, list[str]] = {cat: [] for cat in ATTRIBUTION_CATEGORIES}
    for ref in pit_derive.leaves(factor):
        out.setdefault(role_in_factor(factor, ref), []).append(ref.leaf_id)
    return {k: tuple(sorted(v)) for k, v in out.items() if v}


def check_partition(factor: str) -> tuple[bool, str]:
    """Do the attribution categories PARTITION this factor's leaf set?

    Covering and disjoint, both asserted. Shapley over a non-partition sums to
    the right total while attributing the same input twice, which is worse than
    a wrong total because it looks correct.
    """
    leaves = {ref.leaf_id for ref in pit_derive.leaves(factor)}
    grouped = categorise_leaves(factor)
    if "UNCATEGORISED" in grouped:
        return False, (f"UNCATEGORISED leaves: {', '.join(grouped['UNCATEGORISED'])} "
                       f"-- every primitive needs exactly one attribution category")
    covered: list[str] = []
    for members in grouped.values():
        covered.extend(members)
    missing = leaves - set(covered)
    if missing:
        return False, f"NOT COVERING -- unassigned: {', '.join(sorted(missing))}"
    if len(covered) != len(set(covered)):
        dupes = sorted({x for x in covered if covered.count(x) > 1})
        return False, f"NOT DISJOINT -- in two categories: {', '.join(dupes)}"
    shape = ", ".join(f"{k}:{len(v)}" for k, v in sorted(grouped.items()))
    return True, f"partition holds ({shape})"


def partition_report() -> str:
    out = ["-" * 74,
           "ATTRIBUTION PARTITION -- Shapley categories over the leaf set",
           "-" * 74]
    bad = 0
    for inv in INVARIANTS:
        try:
            ok, detail = check_partition(inv.factor)
        except KeyError:
            continue
        if not ok:
            bad += 1
            out.append(f"  FAIL {inv.factor}: {detail}")
        else:
            out.append(f"  ok   {inv.factor}: {detail}")
    out.append("")
    out.append(f"{bad} partition violation(s).")
    return _NL.join(out)


def report() -> str:
    """Render the specification check and the evidence arithmetic."""
    out: list[str] = []
    out.append("=" * 74)
    out.append(f"ECONOMIC SPECIFICATION -- {SPEC_VERSION}")
    out.append("=" * 74)
    out.append("Each line is a claim about what a factor may depend on,")
    out.append("evaluated against the derivation graph rather than trusted.")
    out.append("")
    results = check_all()
    for res in results:
        mark = "ok  " if res.ok else "FAIL"
        out.append(f"[{mark}] {res.factor}")
        out.append(f"        {res.detail}")
    bad = [r for r in results if not r.ok]
    out.append("")
    out.append(f"{len(results) - len(bad)} of {len(results)} invariants hold.")

    out.append("")
    out.append("-" * 74)
    out.append("MINIMALITY -- factors whose leaves would be double-charged")
    out.append("-" * 74)
    for inv in INVARIANTS:
        try:
            ok, detail = minimality(inv.factor)
        except KeyError:
            continue
        if not ok:
            out.append(f"  {inv.factor}: {detail}")

    out.append("")
    out.append(partition_report())

    out.append("")
    out.append("-" * 74)
    out.append("EVIDENCE -- what each horizon can actually detect")
    out.append("-" * 74)
    out.append("  effective N is measured, not the row count.")
    for label, n_eff, folds in (("12M (annual, measured)", 160.7, 5),
                                ("monthly panel (measured)", 2035.6, 5),
                                ("3M (projected)", 480.0, 5),
                                ("1M (projected)", 1450.0, 5)):
        verdict = evidence_status(n_eff, folds)
        out.append(f"  {label:26s} n_eff {n_eff:8.1f}  {verdict.status}")
        out.append(f"  {'':26s} {verdict.reason}")
    return "\n".join(out)


if __name__ == "__main__":
    print(report())
