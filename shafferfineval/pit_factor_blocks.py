"""pit_factor_blocks -- one factor, one major block. The v2 assembly policy.

OWNER DECISIONS OF 2026-09-21, closing the three execution blockers.

==============================================================================
1. EVERY COMPANY FACTOR HAS EXACTLY ONE MAJOR-BLOCK OWNER
==============================================================================

    CompanyScore = 0.35 E + 0.25 V + 0.15 G + 0.10 Q

No factor may belong to two blocks, even where it is economically relevant to
both. The rule exists because of a measured failure: EBITDA growth previously
lived in BOTH the EBITDA block and the Growth block, giving one economic fact
0.1325 = 15.6% of the company score -- the single largest input in the model --
while appearing in no single place as such. A fact that votes twice through two
major blocks is not weighted, it is double-counted.

    EBITDA growth and EBITDA acceleration belong to E ONLY.

E is therefore "operating strength and EBITDA DEVELOPMENT", and G is "growth
INDEPENDENT of EBITDA duplication". The ownership question is

    what economic concept does this factor measure?

and never

    what noun appears in its name?

which is why `ebitda_growth` sits in E despite the word "growth", and why a
genuine EPS-growth factor would sit in G despite measuring earnings.

==============================================================================
2. THE MAP, BY ECONOMIC QUESTION
==============================================================================

    E  Is the operating engine strong and improving?
    V  What are we paying for the business?
    G  Is the business genuinely expanding?
    Q  How healthy and reliable are the business and the balance sheet?

SECTOR FACTORS ARE NOT IN EVGQ AT ALL. The sector is a separate [-15, +15]
overlay, and the final score is

    ShafferScore = CompanyScore + SectorScore

Twelve of the fourteen factor specs map cleanly. TWO ARE REFUSED rather than
placed where there is room -- see `UNMAPPED` -- because the instruction was
explicit: a factor that does not fit a block definition cleanly gets reviewed,
not assigned.

==============================================================================
3. MIN_BLOCK_WEIGHT = 0.40, AND WHY THAT NUMBER
==============================================================================

    sum(w for blocks with usable information)  >=  0.40

The largest block is E at 0.35. So the threshold has a STRUCTURAL meaning
rather than being a number someone liked:

    NO SINGLE BLOCK CAN PRODUCE A SHAFFER COMPANY SCORE BY ITSELF.

At least two major blocks must carry usable information -- necessary, and not
sufficient: V + Q = 0.35 and G + Q = 0.25 are two blocks each and both fall
short. Below the floor nothing is emitted and the row says

    PARTIAL_SCORE_INSUFFICIENT_BLOCK_COVERAGE

WEIGHTS STILL DO NOT REDISTRIBUTE. Missing V, the score is exactly

    0.35 E + 0.15 G + 0.10 Q

and NOT that quantity over 0.60. The row carries coverage 0.60/0.85 = 70.59%
and its block mask, and it sits on a smaller scale than a full one. Losing a
block must shrink the score, never promote the survivors.

Stdlib only. Reads no database.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Mapping, Optional

import pit_factor_spec
import pit_score_signature

__all__ = [
    "BLOCK_MAP_VERSION", "BLOCK_E", "BLOCK_V", "BLOCK_G", "BLOCK_Q", "BLOCKS",
    "BLOCK_QUESTION", "FACTOR_BLOCK", "UNMAPPED", "SECTOR_IS_NOT_A_BLOCK",
    "MIN_BLOCK_WEIGHT", "INSUFFICIENT_BLOCK_COVERAGE",
    "FactorMapError", "block_of", "factors_in", "block_weight",
    "available_block_weight", "block_coverage", "meets_min_block_weight",
    "assemble", "block_mask", "policy_record", "validate",
    "POLICY_WEIGHTS_VERSION", "POLICY_WEIGHTS_STATUS", "ROLE_SCORING",
    "ROLE_ZERO_WEIGHT_CHALLENGER", "FACTOR_ROLE", "ZERO_WEIGHT_WHY",
    "WITHIN_BLOCK_WEIGHTS", "WITHIN_BLOCK_REASON", "within_block_weights",
    "is_scoring", "effective_company_weight", "block_score",
    "V_FACTOR_SUBFACTORS",
    "DIRECTION_POLICY_VERSION", "HIGHER_IS_BETTER", "LOWER_IS_BETTER",
    "EMBEDDED_IN_TRANSFORM", "UNDECLARED", "DIRECTION_IS_ORIENTATION_ONLY",
    "FACTOR_DIRECTION_V1", "SUBFACTOR_DIRECTION_V1", "DIRECTION_SOURCE",
    "direction_of",
]

BLOCK_MAP_VERSION = "factor_block_map_v1"

#: Imported, never restated: the blocks and their weights are owned by
#: `pit_score_signature` and a second spelling would be a second model.
BLOCK_E = pit_score_signature.V2_BLOCK_EBITDA
BLOCK_V = pit_score_signature.V2_BLOCK_VALUATION
BLOCK_G = pit_score_signature.V2_BLOCK_GROWTH
BLOCK_Q = pit_score_signature.V2_BLOCK_QUALITY
BLOCKS = pit_score_signature.V2_BLOCKS

BLOCK_QUESTION: dict[str, str] = {
    BLOCK_E: "Is the operating engine strong and improving?",
    BLOCK_V: "What are we paying for the business?",
    BLOCK_G: "Is the business genuinely expanding?",
    BLOCK_Q: "How healthy and reliable are the business and the balance sheet?",
}


class FactorMapError(KeyError):
    """A factor has no block, two blocks, or a block that does not exist."""


# ==========================================================================
# THE MAP
# ==========================================================================

#: factor spec key -> the ONE block that owns it.
FACTOR_BLOCK: dict[str, str] = {
    # ---- E: operating strength and EBITDA development ---------------------
    "ebitda_benchmark": BLOCK_E,
    "ebitda_scale": BLOCK_E,
    "ebitda_efficiency": BLOCK_E,
    "ebitda_growth": BLOCK_E,
    "ebitda_acceleration": BLOCK_E,

    # ---- V: what we are paying --------------------------------------------
    "pe_ratio": BLOCK_V,
    "ev_ebitda": BLOCK_V,

    # ---- G: expansion independent of EBITDA -------------------------------
    "real_revenue_growth": BLOCK_G,

    # ---- Q: health and reliability ----------------------------------------
    "net_debt_ebitda": BLOCK_Q,
    "debt_market_cap": BLOCK_Q,
    "interest_coverage": BLOCK_Q,
    "fcf_conversion": BLOCK_Q,
}

#: Why each factor sits where it sits, keyed by the ECONOMIC QUESTION the spec
#: itself asks -- quoted from `pit_factor_spec`, not paraphrased here.
FACTOR_REASON: dict[str, str] = {
    "ebitda_benchmark": (
        "'What does a right-sized peer in this sector earn?' -- excess over "
        "the benchmark IS the operating-strength reading."),
    "ebitda_scale": (
        "'How many median peers would it take to make this company?' -- the "
        "size of the operating engine."),
    "ebitda_efficiency": (
        "'How profitable is a typical right-sized peer, per dollar of "
        "sales?' -- margin, i.e. engine quality per unit of activity."),
    "ebitda_growth": (
        "'Is the earnings power larger than it was a year ago?' -- EBITDA "
        "DEVELOPMENT, which is E's second half. It is in E and NOT in G, and "
        "that single placement is what removes the 15.6% double count."),
    "ebitda_acceleration": (
        "'Is the earnings power growing FASTER than it was?' -- second "
        "derivative of the same engine; same owner as the first."),
    "pe_ratio": (
        "'How many years of current earnings is the market paying?' -- a "
        "price question, therefore V."),
    "ev_ebitda": (
        "'What multiple is the market paying for a right-sized peer's "
        "earnings?' -- a price question, therefore V. Note it reaches EBITDA "
        "as an INPUT while asking about price; the owner is decided by the "
        "question, not by the ingredient list."),
    "real_revenue_growth": (
        "'Did the business sell MORE, or just charge more?' -- genuine "
        "expansion, deflated, and entirely free of EBITDA. It is the one "
        "factor that makes G a non-EBITDA growth block rather than a second "
        "copy of E."),
    "net_debt_ebitda": (
        "'How many years of earnings would clear the debt?' -- leverage. The "
        "EBITDA denominator does not make it an E factor: it measures the "
        "BURDEN, not the engine."),
    "debt_market_cap": (
        "'How much leverage sits under each dollar of equity value?' -- "
        "leverage again, against a market denominator. Not V: it asks about "
        "the balance sheet, not about what we are paying."),
    "interest_coverage": (
        "'How many times over can the business pay its interest bill?' -- "
        "balance-sheet strength, the classic solvency reading."),
    "fcf_conversion": (
        "'How much of the accounting earnings turns into spendable cash?' -- "
        "earnings QUALITY in the strict sense: the gap between reported "
        "profit and cash is exactly what Q exists to notice."),
}


#: REFUSED, not assigned. The instruction was explicit: a factor that does not
#: fit a block definition cleanly is REVIEWED, not dropped into whichever block
#: has room. Neither block here is short of ingredients -- E has five factors
#: and Q has four -- so this is a definitional refusal, not a capacity one.
UNMAPPED: dict[str, dict[str, Any]] = {
    "roa": {
        "status": "REFUSED_PENDING_REVIEW",
        "question": "How much profit does each dollar of assets generate?",
        "why_not_E": (
            "E is EBITDA-centred BY DESIGN, and the design reason is to strip "
            "capital structure and tax out of the operating assessment. ROA's "
            "numerator is NET INCOME -- after interest and after tax -- so "
            "admitting it to E would re-import precisely what E was built to "
            "exclude, inside the block that carries 0.35."),
        "why_not_Q": (
            "Q is leverage, balance-sheet strength and earnings/cash quality. "
            "A return on capital is none of those three: it is a "
            "PROFITABILITY measure, and calling it quality would stretch the "
            "block definition to fit the factor rather than the other way "
            "round."),
        "what_would_resolve_it": (
            "Either an EBITDA-based restatement (EBITDA per dollar of assets), "
            "which would sit in E cleanly and is close to what "
            "`ebitda_efficiency` already does per dollar of SALES -- raising "
            "the question of whether both are wanted -- or a deliberate "
            "decision that v2 carries a fifth concept, which is a weight "
            "decision and therefore the owner's."),
    },
    "roe": {
        "status": "REFUSED_PENDING_REVIEW",
        "question": "How much profit does each dollar of shareholder capital "
                    "generate?",
        "why_not_E": (
            "Everything said of ROA, plus a sharper problem: the equity "
            "denominator makes ROE MECHANICALLY RISE WITH LEVERAGE. A block "
            "meant to measure the operating engine would reward balance-sheet "
            "risk."),
        "why_not_Q": (
            "THE SIGN INVERSION IS THE REAL OBJECTION. Q already contains "
            "net_debt_ebitda and debt_market_cap, both of which score WORSE "
            "as leverage rises. ROE scores BETTER as leverage rises. Putting "
            "it in Q would place two opposing readings of the same underlying "
            "fact inside one block, where they would partially cancel and the "
            "cancellation would be invisible in the block score."),
        "what_would_resolve_it": (
            "A de-levered return measure, or an explicit decision that ROE is "
            "research-only. It should NOT be resolved by picking whichever "
            "block has room."),
    },
}

SECTOR_IS_NOT_A_BLOCK = (
    "Sector factors do not belong to EVGQ. The sector is a separate overlay "
    "on [-15, +15] and the final score is ShafferScore = CompanyScore + "
    "SectorScore. Folding a sector reading into a company block would spend "
    "the overlay's range twice and would make the company score depend on "
    "where the company is listed rather than on how it trades."
)


# ==========================================================================
# THE COVERAGE FLOOR
# ==========================================================================

#: Minimum total DECLARED weight that must carry usable information before any
#: company score is emitted. Structural: the largest block is E at 0.35, so no
#: single block can clear this on its own.
MIN_BLOCK_WEIGHT = 0.40

INSUFFICIENT_BLOCK_COVERAGE = "PARTIAL_SCORE_INSUFFICIENT_BLOCK_COVERAGE"

MIN_BLOCK_WEIGHT_WHY = (
    "0.40 against a largest block of 0.35: NO SINGLE BLOCK CAN PRODUCE A "
    "COMPANY SCORE BY ITSELF. At least two blocks must carry usable "
    "information -- necessary and NOT sufficient, since V+Q = 0.35 and "
    "G+Q = 0.25 are two blocks each and both fall short. That structural "
    "property is the defence of the number; it was not chosen for its "
    "roundness."
)


def block_of(factor_key: str) -> str:
    """The one block that owns this factor. Raises on an unmapped factor."""
    if factor_key in UNMAPPED:
        raise FactorMapError(
            "%r is REFUSED_PENDING_REVIEW and has no block: %s"
            % (factor_key, UNMAPPED[factor_key]["why_not_Q"]))
    if factor_key not in FACTOR_BLOCK:
        raise FactorMapError(
            "%r has no major-block owner. Every company factor has exactly "
            "one; add it deliberately or record it in UNMAPPED with the "
            "reason it does not fit." % (factor_key,))
    return FACTOR_BLOCK[factor_key]


def factors_in(block: str) -> tuple[str, ...]:
    """The factors a block owns, in declaration order."""
    if block not in BLOCKS:
        raise FactorMapError("%r is not a v2 block" % (block,))
    return tuple(k for k, b in FACTOR_BLOCK.items() if b == block)


def block_weight(block: str) -> float:
    if block not in BLOCKS:
        raise FactorMapError("%r is not a v2 block" % (block,))
    return pit_score_signature.V2_MAJOR_WEIGHTS[block]


def available_block_weight(available: Iterable[str]) -> float:
    """Total DECLARED weight of the blocks that carry usable information."""
    seen = {b for b in available}
    unknown = seen - set(BLOCKS)
    if unknown:
        raise FactorMapError("not v2 blocks: %s" % sorted(unknown))
    return sum(block_weight(b) for b in seen)


def block_coverage(available: Iterable[str]) -> float:
    """Available weight as a fraction of the model's own declared total."""
    total = sum(pit_score_signature.V2_MAJOR_WEIGHTS.values())
    return available_block_weight(available) / total if total > 0 else 0.0


def meets_min_block_weight(available: Iterable[str]) -> bool:
    return available_block_weight(available) + 1e-12 >= MIN_BLOCK_WEIGHT


def block_mask(available: Iterable[str]) -> str:
    """'E=1,V=0,G=1,Q=1' -- which blocks carried information."""
    seen = {b for b in available}
    letter = pit_score_signature.V2_BLOCK_LETTER
    return ",".join("%s=%d" % (letter[b], 1 if b in seen else 0)
                    for b in BLOCKS)


def assemble(block_scores: Mapping[str, Optional[float]]) -> dict[str, Any]:
    """The company score, or a refusal. NO RENORMALISATION, ever.

    Returns a record carrying the score, the mask, the coverage and -- when the
    floor is not met -- the refusal reason and NO score. An absence is never a
    zero.
    """
    unknown = set(block_scores) - set(BLOCKS)
    if unknown:
        raise FactorMapError(
            "not v2 blocks: %s. v1's pillar names are a different model's "
            "vocabulary." % sorted(unknown))
    available = [b for b in BLOCKS if block_scores.get(b) is not None]
    carried = available_block_weight(available)
    coverage = block_coverage(available)
    mask = block_mask(available)

    if not meets_min_block_weight(available):
        return {
            "policy_version": BLOCK_MAP_VERSION,
            "company_score": None,
            "signature": (pit_score_signature.v2_signature_of(
                {b: block_scores.get(b) for b in BLOCKS}) if available
                else "NONE"),
            "block_mask": mask,
            "available_block_weight": carried,
            "block_coverage": coverage,
            "refused": INSUFFICIENT_BLOCK_COVERAGE,
            "why": ("%.2f of declared weight carries information; the floor is "
                    "%.2f. %s" % (carried, MIN_BLOCK_WEIGHT,
                                  MIN_BLOCK_WEIGHT_WHY)),
        }

    scored = pit_score_signature.score_v2(
        {b: block_scores.get(b) for b in BLOCKS})
    return {
        "policy_version": BLOCK_MAP_VERSION,
        "company_score": scored.company_score,
        "raw_score": scored.raw_score,
        "signature": scored.signature,
        "block_mask": mask,
        "available_block_weight": carried,
        "block_coverage": coverage,
        "effective_weights": dict(scored.effective_weights),
        "partial_score_marker": scored.partial_score_marker,
        "refused": None,
        "renormalised": False,
    }




# ==========================================================================
# WITHIN-BLOCK WEIGHTS -- 'POLICY_WEIGHTS_V1', NOT EMPIRICALLY OPTIMISED
#
# A TRANSPARENT STARTING HYPOTHESIS for the diagnostic replay. These are
# POLICY weights: they were argued, not fitted, and nothing here claims
# 35/30/25/10 is optimal. The version string carries that, so a later reader
# cannot mistake a convention for a result.
# ==========================================================================

POLICY_WEIGHTS_VERSION = "POLICY_WEIGHTS_V1"
POLICY_WEIGHTS_STATUS = "NOT_EMPIRICALLY_OPTIMIZED"

#: A factor that carries weight in its block's score.
ROLE_SCORING = "SCORING"

#: OWNED by its block, economically at home there, and carrying ZERO scoring
#: weight in this version. NOT the same as REFUSED: a refused factor has no
#: block, this one has a block and has not earned weight in it.
ROLE_ZERO_WEIGHT_CHALLENGER = "SCORING_WEIGHT_ZERO_CHALLENGER"

FACTOR_ROLE: dict[str, str] = {
    k: ROLE_SCORING for k in FACTOR_BLOCK
}
FACTOR_ROLE["ebitda_scale"] = ROLE_ZERO_WEIGHT_CHALLENGER

ZERO_WEIGHT_WHY: dict[str, str] = {
    "ebitda_scale": (
        "'How many median peers would it take to make this company?' is a "
        "SIZE question. Raw EBITDA scale and monotone transformations of it "
        "can masquerade as economic signal while primarily rewarding bigness, "
        "and a large company is not a good investment for being large. It is "
        "economically at home in E -- the operating engine's size is part of "
        "describing the engine -- so it is not REFUSED; it simply has not "
        "earned scoring weight. Giving it 0.10 or 0.20 merely because the "
        "factor exists would be exactly the undefended assignment this "
        "project refuses elsewhere."),
}

#: Within-block weights. Each block's SCORING factors sum to 1.00; zero-weight
#: challengers appear with 0.00 so their ownership is visible in the table
#: rather than inferred from an absence.
WITHIN_BLOCK_WEIGHTS: dict[str, dict[str, float]] = {
    BLOCK_E: {
        "ebitda_benchmark": 0.35,
        "ebitda_growth": 0.25,
        "ebitda_efficiency": 0.20,
        "ebitda_acceleration": 0.20,
        "ebitda_scale": 0.00,
    },
    BLOCK_V: {
        # Finer granularity than the two V factor specs on purpose: the
        # valuation pillar decomposes pe_ratio into an ABSOLUTE leg and a
        # PEER-RELATIVE leg, and those are different economic questions. Owned
        # by pit_valuation_spec.SUBFACTOR_WEIGHTS and imported, never restated.
    },
    BLOCK_G: {
        "real_revenue_growth": 1.00,
    },
    BLOCK_Q: {
        "fcf_conversion": 0.35,
        "interest_coverage": 0.30,
        "net_debt_ebitda": 0.25,
        "debt_market_cap": 0.10,
    },
}

WITHIN_BLOCK_REASON: dict[str, str] = {
    BLOCK_E: (
        "The split already published in the candidate document: benchmark "
        "excess 0.35, growth 0.25, efficiency 0.20, acceleration 0.20. Those "
        "four reproduce the document's own effective weights exactly -- "
        "12.25%, 8.75%, 7.00%, 7.00% of the company score -- so adopting them "
        "keeps the published calibration and the model in agreement rather "
        "than quietly replacing one with the other."),
    BLOCK_Q: (
        "THE REASONING MATTERS MORE THAN THE DECIMALS. fcf_conversion leads at "
        "0.35 because it supplies what the other three do not: whether "
        "reported operating performance is turning into CASH. "
        "interest_coverage at 0.30 directly measures the ability to service "
        "financing. net_debt_ebitda at 0.25 measures leverage against the "
        "operating engine. debt_market_cap gets only 0.10 because it overlaps "
        "the leverage question AND imports market-price movement into a block "
        "meant to describe financial quality -- if the stock collapses while "
        "the balance sheet is unchanged, debt/market-cap deteriorates "
        "MECHANICALLY. Useful, and it must not dominate. The net effect is "
        "deliberate: three debt-related measures are prevented from turning Q "
        "into a disguised leverage block."),
    BLOCK_G: (
        "One factor, weight 1.00 by force rather than by choice. G HAS A "
        "GENUINE SINGLE-POINT DEPENDENCY: if real_revenue_growth is "
        "unavailable, G is unavailable, and there is no second reading to "
        "fall back on. That is preferable to manufacturing a second factor so "
        "the architecture looks symmetric. EPS growth may become a challenger "
        "later, now that its share-basis machinery is correct; it is not "
        "smuggled into this specification to fill a gap."),
    BLOCK_V: (
        "Owned by pit_valuation_spec.SUBFACTOR_WEIGHTS -- pe_absolute 0.50, "
        "pe_relative 0.30, ev_ebitda_supplement 0.20 -- and imported here so "
        "there is exactly one copy."),
}


def within_block_weights(block: str) -> dict[str, float]:
    """The within-block weights, SCORING factors only.

    V is read live from `pit_valuation_spec`; the others are declared above.
    Zero-weight challengers are EXCLUDED from the returned table, so they
    cannot reach a denominator by accident.
    """
    if block not in BLOCKS:
        raise FactorMapError("%r is not a v2 block" % (block,))
    if block == BLOCK_V:
        import pit_valuation_spec
        return dict(pit_valuation_spec.SUBFACTOR_WEIGHTS)
    return {k: w for k, w in WITHIN_BLOCK_WEIGHTS[block].items() if w > 0.0}


#: V IS DECLARED AT A FINER GRANULARITY THAN ITS FACTOR SPECS, and the mapping
#: is stated rather than left to be inferred. `pit_valuation_spec` decomposes
#: the P/E question into an ABSOLUTE leg and a PEER-RELATIVE leg because they
#: are different economic questions asked of one multiple; `pit_factor_spec`
#: has a single `pe_ratio` spec covering both. Neither is wrong, and the two
#: levels must be reconciled EXPLICITLY or a weight goes missing.
V_FACTOR_SUBFACTORS: dict[str, tuple[str, ...]] = {
    "pe_ratio": ("pe_absolute", "pe_relative"),
    "ev_ebitda": ("ev_ebitda_supplement",),
}


def is_scoring(factor_key: str) -> bool:
    """Does this factor carry weight, or is it a zero-weight challenger?"""
    return FACTOR_ROLE.get(factor_key, ROLE_SCORING) == ROLE_SCORING


def effective_company_weight(factor_key: str) -> float:
    """This factor's share of the WHOLE company score. 0.0 for a challenger."""
    block = block_of(factor_key)
    if not is_scoring(factor_key):
        return 0.0
    weights = within_block_weights(block)
    if block == BLOCK_V:
        subs = V_FACTOR_SUBFACTORS.get(factor_key)
        if subs is None:
            raise FactorMapError(
                "%r is a V factor with no declared subfactor correspondence; "
                "V's weights live at subfactor granularity and the mapping "
                "must be explicit" % (factor_key,))
        return block_weight(block) * sum(weights[k] for k in subs)
    return block_weight(block) * weights[factor_key]


def block_score(block: str,
                factor_scores: Mapping[str, Optional[float]]
                ) -> dict[str, Any]:
    """One block's score from its factors, or None.

    WITHIN-block renormalisation is ALLOWED and cross-block renormalisation is
    not. The distinction is not arbitrary: several measurements of one thing
    may stand in for one another, whereas an entire missing block is a
    statement about a DIFFERENT thing and must shrink the score instead of
    promoting the survivors.

    A zero-weight challenger contributes nothing: not to the score, not to
    availability, and not to the denominator. It is reported separately so its
    value is still observable for research.
    """
    weights = within_block_weights(block)
    unknown = set(factor_scores) - set(WITHIN_BLOCK_WEIGHTS.get(block, {}))         - set(weights)
    if unknown:
        raise FactorMapError(
            "factors not owned by %s: %s" % (block, sorted(unknown)))

    present = {k: float(v) for k, v in factor_scores.items()
               if k in weights and v is not None}
    challengers = {k: v for k, v in factor_scores.items()
                   if k in FACTOR_ROLE and not is_scoring(k)}

    if not present:
        return {"block": block, "score": None, "available": False,
                "n_scoring_present": 0,
                "n_scoring_declared": len(weights),
                "within_block_coverage": 0.0,
                "renormalised_over": 0.0,
                "challengers_observed": challengers,
                "unavailable_reason": "no scoring factor available"}

    carried = sum(weights[k] for k in present)
    score = sum(weights[k] * present[k] for k in present) / carried
    return {"block": block, "score": score, "available": True,
            "n_scoring_present": len(present),
            "n_scoring_declared": len(weights),
            "within_block_coverage": carried,
            "renormalised_over": carried,
            "challengers_observed": challengers,
            "unavailable_reason": None}




# ==========================================================================
# FACTOR DIRECTION -- 'FACTOR_DIRECTION_V1', owner-approved 2026-09-22
#
# A wrong sign inverts a factor's contribution SILENTLY and fails no test that
# exists. The survey found direction declared for only six factors, under a
# key vocabulary matching none of FACTOR_BLOCK's, and for the two Q leverage
# factors only inside the frozen v1 production module. So direction is
# declared HERE, once, per factor key, and it is a FROZEN COMPONENT.
#
# THE OWNER'S RULE, verbatim in spirit: direction is ORIENTATION ONLY, applied
# AFTER domain-validity and refusal checks. It does not override invalid-input
# handling. A nonsensical interest-coverage denominator is REFUSED, never
# rewarded because the direction happens to be "higher".
# ==========================================================================

DIRECTION_POLICY_VERSION = "FACTOR_DIRECTION_V1"

HIGHER_IS_BETTER = "HIGHER_IS_BETTER"
LOWER_IS_BETTER = "LOWER_IS_BETTER"

#: The sign is CARRIED BY A SIGNED TRANSFORM rather than applied afterwards.
#: pit_valuation_spec's convex P/E transform already returns a negative score
#: for an expensive multiple; orienting its output again would double-apply.
EMBEDDED_IN_TRANSFORM = "EMBEDDED_IN_TRANSFORM"

#: Deliberately reachable, so an undeclared factor FAILS validate() rather than
#: silently defaulting to "higher".
UNDECLARED = "UNDECLARED"

DIRECTION_IS_ORIENTATION_ONLY = (
    "Direction is orientation applied AFTER domain-validity and refusal "
    "checks; it never overrides invalid-input handling. A factor whose inputs "
    "fail their domain rule is REFUSED with its reason, regardless of which "
    "way 'better' points."
)

# Adopted from pit_normalization.candidate_v2_registry() where the registry
# already declares a direction. Read live, never re-typed: the registry key
# vocabulary differs from FACTOR_BLOCK's, and this is the one place the two
# are reconciled.
_REGISTRY_KEY: dict[str, str] = {
    "ebitda_benchmark": "EBITDAExcessLevel",
    "ebitda_efficiency": "EBITDAMarginRank",
    "ebitda_growth": "EBITDAGrowth",
    "ebitda_acceleration": "EBITDAAcceleration",
    "ebitda_scale": "EBITDAScale",
    "real_revenue_growth": "RealRevenueGrowth",
}


def _from_registry(factor_key: str) -> str:
    import pit_normalization
    reg = pit_normalization.candidate_v2_registry()
    rk = _REGISTRY_KEY.get(factor_key)
    if rk is None or rk not in reg:
        return UNDECLARED
    return HIGHER_IS_BETTER if reg[rk].higher_is_better else LOWER_IS_BETTER


FACTOR_DIRECTION_V1: dict[str, str] = {
    # ---- OWNER-APPROVED, 2026-09-22 ---------------------------------------
    "ev_ebitda": LOWER_IS_BETTER,
    "fcf_conversion": HIGHER_IS_BETTER,
    "interest_coverage": HIGHER_IS_BETTER,
    "net_debt_ebitda": LOWER_IS_BETTER,
    "debt_market_cap": LOWER_IS_BETTER,
    # ---- ADOPTED from the normalization registry's existing declarations --
    "ebitda_benchmark": _from_registry("ebitda_benchmark"),
    "ebitda_efficiency": _from_registry("ebitda_efficiency"),
    "ebitda_growth": _from_registry("ebitda_growth"),
    "ebitda_acceleration": _from_registry("ebitda_acceleration"),
    "ebitda_scale": _from_registry("ebitda_scale"),
    "real_revenue_growth": _from_registry("real_revenue_growth"),
    # ---- carried by the signed valuation transform -------------------------
    "pe_ratio": EMBEDDED_IN_TRANSFORM,
}

#: The valuation SUBFACTORS, at their own granularity. ev_ebitda_supplement
#: is what the owner approved by name; the factor-level key above is its
#: factor-spec alias via V_FACTOR_SUBFACTORS.
SUBFACTOR_DIRECTION_V1: dict[str, str] = {
    "pe_absolute": EMBEDDED_IN_TRANSFORM,
    "pe_relative": EMBEDDED_IN_TRANSFORM,
    "ev_ebitda_supplement": LOWER_IS_BETTER,
}

DIRECTION_SOURCE: dict[str, str] = {
    "ev_ebitda": "owner decision 2026-09-22 (as ev_ebitda_supplement)",
    "fcf_conversion": "owner decision 2026-09-22",
    "interest_coverage": "owner decision 2026-09-22",
    "net_debt_ebitda": "owner decision 2026-09-22; agrees with the frozen v1 "
                       "company_scoring.COMPONENT_HIGHER_IS_BETTER",
    "debt_market_cap": "owner decision 2026-09-22; agrees with the frozen v1 "
                       "company_scoring.COMPONENT_HIGHER_IS_BETTER",
    "ebitda_benchmark": "pit_normalization registry: EBITDAExcessLevel",
    "ebitda_efficiency": "pit_normalization registry: EBITDAMarginRank",
    "ebitda_growth": "pit_normalization registry: EBITDAGrowth",
    "ebitda_acceleration": "pit_normalization registry: EBITDAAcceleration",
    "ebitda_scale": "pit_normalization registry: EBITDAScale (challenger; "
                    "direction recorded for research, never scored)",
    "real_revenue_growth": "pit_normalization registry: RealRevenueGrowth",
    "pe_ratio": "pit_valuation_spec convex transform, sign embedded",
}


def direction_of(factor_key: str) -> str:
    """The declared direction. RAISES on an undeclared factor -- never defaults."""
    d = FACTOR_DIRECTION_V1.get(factor_key, UNDECLARED)
    if d == UNDECLARED:
        raise FactorMapError(
            "%r has no declared direction under %s. Declare it deliberately; "
            "a default sign is a silent modelling decision."
            % (factor_key, DIRECTION_POLICY_VERSION))
    return d


def policy_record() -> dict[str, Any]:
    """The whole assembly policy, for a replay run to store."""
    return {
        "version": BLOCK_MAP_VERSION,
        "blocks": {b: {"letter": pit_score_signature.V2_BLOCK_LETTER[b],
                       "weight": block_weight(b),
                       "question": BLOCK_QUESTION[b],
                       "factors": list(factors_in(b))}
                   for b in BLOCKS},
        "direction_policy_version": DIRECTION_POLICY_VERSION,
        "factor_direction": dict(FACTOR_DIRECTION_V1),
        "subfactor_direction": dict(SUBFACTOR_DIRECTION_V1),
        "direction_source": dict(DIRECTION_SOURCE),
        "direction_rule": DIRECTION_IS_ORIENTATION_ONLY,
        "policy_weights_version": POLICY_WEIGHTS_VERSION,
        "policy_weights_status": POLICY_WEIGHTS_STATUS,
        "within_block_weights": {b: within_block_weights(b) for b in BLOCKS},
        "within_block_reasons": dict(WITHIN_BLOCK_REASON),
        "factor_roles": dict(FACTOR_ROLE),
        "zero_weight_why": dict(ZERO_WEIGHT_WHY),
        "effective_company_weights": {
            k: effective_company_weight(k) for k in FACTOR_BLOCK},
        "one_factor_one_block": (
            "Every company factor has exactly one major-block owner. No "
            "factor may be assigned to two blocks, even where it is "
            "economically relevant to both."),
        "why_the_rule_exists": (
            "EBITDA growth previously lived in both E and G, giving one "
            "economic fact 0.1325 = 15.6% of the company score -- the largest "
            "single input -- while appearing nowhere as such."),
        "factor_reasons": dict(FACTOR_REASON),
        "unmapped": json.loads(json.dumps(UNMAPPED)),
        "sector": SECTOR_IS_NOT_A_BLOCK,
        "min_block_weight": MIN_BLOCK_WEIGHT,
        "min_block_weight_why": MIN_BLOCK_WEIGHT_WHY,
        "insufficient_reason": INSUFFICIENT_BLOCK_COVERAGE,
        "no_renormalisation": (
            "Missing V, the score is exactly 0.35E + 0.15G + 0.10Q and NOT "
            "that quantity over 0.60. Coverage 0.60/0.85 = 70.59% and the "
            "block mask travel with the row."),
    }


def validate() -> list[str]:
    """Self-check. Returns problems; empty means PASS."""
    problems: list[str] = []
    spec_keys = {x.key for x in pit_factor_spec.SPECS}

    # ---- every factor accounted for, exactly once ------------------------
    mapped = set(FACTOR_BLOCK)
    refused = set(UNMAPPED)
    if mapped & refused:
        problems.append("a factor is both mapped and refused: %s"
                        % sorted(mapped & refused))
    missing = spec_keys - mapped - refused
    if missing:
        problems.append("factor specs with no block and no refusal: %s"
                        % sorted(missing))
    extra = (mapped | refused) - spec_keys
    if extra:
        problems.append("mapped keys that are not factor specs: %s"
                        % sorted(extra))
    for key, block in FACTOR_BLOCK.items():
        if block not in BLOCKS:
            problems.append("%s maps to %r, which is not a v2 block"
                            % (key, block))

    # ---- the double count is gone ----------------------------------------
    for key in ("ebitda_growth", "ebitda_acceleration"):
        if FACTOR_BLOCK.get(key) != BLOCK_E:
            problems.append("%s must be owned by E ONLY -- that placement is "
                            "what removes the 15.6%% double count" % key)
    if any(FACTOR_BLOCK.get(k) == BLOCK_G for k in FACTOR_BLOCK
           if k.startswith("ebitda")):
        problems.append("no EBITDA factor may sit in G; G is the NON-EBITDA "
                        "growth block")
    if not factors_in(BLOCK_G):
        problems.append("G owns no factor at all; it would be a declared "
                        "weight with nothing behind it")

    # ---- the floor -------------------------------------------------------
    largest = max(pit_score_signature.V2_MAJOR_WEIGHTS.values())
    if largest >= MIN_BLOCK_WEIGHT:
        problems.append("MIN_BLOCK_WEIGHT must exceed the largest single block "
                        "weight, or one block could score alone")
    for block in BLOCKS:
        if meets_min_block_weight([block]):
            problems.append("%s clears the floor alone" % block)
    if not meets_min_block_weight([BLOCK_E, BLOCK_Q]):
        problems.append("E+Q = 0.45 must clear the floor")
    for pair in ((BLOCK_V, BLOCK_Q), (BLOCK_G, BLOCK_Q)):
        if meets_min_block_weight(pair):
            problems.append("%s should NOT clear the floor -- two blocks is "
                            "necessary, not sufficient" % (pair,))

    # ---- no renormalisation ---------------------------------------------
    full = assemble({b: 10.0 for b in BLOCKS})
    no_v = assemble({BLOCK_E: 10.0, BLOCK_V: None, BLOCK_G: 10.0,
                     BLOCK_Q: 10.0})
    if full["company_score"] is None or no_v["company_score"] is None:
        problems.append("both of these must score")
    else:
        want = 0.35 * 10.0 + 0.15 * 10.0 + 0.10 * 10.0
        if abs(no_v["company_score"] - want) > 1e-9:
            problems.append("a missing block must NOT be redistributed")
        if abs(no_v["block_coverage"] - 0.60 / 0.85) > 1e-9:
            problems.append("coverage must be 0.60/0.85 = 70.59%")
        for b in (BLOCK_E, BLOCK_G, BLOCK_Q):
            if abs(full["effective_weights"][b]
                   - no_v["effective_weights"][b]) > 1e-12:
                problems.append("losing V moved %s's effective weight" % b)

    # ---- the refusal is a refusal, not a zero ----------------------------
    thin = assemble({BLOCK_E: None, BLOCK_V: 50.0, BLOCK_G: None,
                     BLOCK_Q: 50.0})
    if thin["company_score"] is not None:
        problems.append("V+Q = 0.35 is below the floor and must emit NO score")
    if thin["refused"] != INSUFFICIENT_BLOCK_COVERAGE:
        problems.append("a refusal must name INSUFFICIENT_BLOCK_COVERAGE")

    # ---- the refused factors stay refused --------------------------------
    for key in UNMAPPED:
        try:
            block_of(key)
        except FactorMapError:
            pass
        else:
            problems.append("%s is REFUSED and block_of() returned a block"
                            % key)
        if "why_not_E" not in UNMAPPED[key] or "why_not_Q" not in UNMAPPED[key]:
            problems.append("%s must say why it fits NEITHER candidate block"
                            % key)

    if "overlay" not in SECTOR_IS_NOT_A_BLOCK:
        problems.append("the sector exclusion must name the overlay")

    # ---- within-block weights -------------------------------------------
    for block in BLOCKS:
        w = within_block_weights(block)
        if not w:
            problems.append("%s has no scoring factors" % block)
            continue
        if abs(sum(w.values()) - 1.0) > 1e-12:
            problems.append("%s's within-block weights must sum to 1.00, not "
                            "%.4f" % (block, sum(w.values())))
        if any(v <= 0.0 for v in w.values()):
            problems.append("%s: a scoring weight must be positive" % block)

    # every scoring factor of a block is declared in its table
    for key, block in FACTOR_BLOCK.items():
        if block == BLOCK_V:
            continue          # V's granularity is the valuation subfactors
        table = WITHIN_BLOCK_WEIGHTS[block]
        if key not in table:
            problems.append("%s is owned by %s and has no within-block weight"
                            % (key, block))

    # ---- the zero-weight challenger --------------------------------------
    if FACTOR_ROLE.get("ebitda_scale") != ROLE_ZERO_WEIGHT_CHALLENGER:
        problems.append("ebitda_scale must be a zero-weight challenger")
    if "ebitda_scale" in within_block_weights(BLOCK_E):
        problems.append("a zero-weight challenger must NOT reach the "
                        "within-block denominator")
    if block_of("ebitda_scale") != BLOCK_E:
        problems.append("a challenger keeps its block; it is not refused")
    if effective_company_weight("ebitda_scale") != 0.0:
        problems.append("a challenger's effective company weight is 0.0")
    if "ebitda_scale" not in ZERO_WEIGHT_WHY:
        problems.append("a zero weight must carry its reason")
    only_challenger = block_score(BLOCK_E, {"ebitda_scale": 90.0})
    if only_challenger["available"] or only_challenger["score"] is not None:
        problems.append("a challenger alone must NOT make its block available")
    if only_challenger["challengers_observed"].get("ebitda_scale") != 90.0:
        problems.append("a challenger's value must stay observable")

    # ---- V's two granularities must reconcile exactly --------------------
    v_weights = within_block_weights(BLOCK_V)
    claimed = [k for subs in V_FACTOR_SUBFACTORS.values() for k in subs]
    if sorted(claimed) != sorted(v_weights):
        problems.append("the V factor/subfactor correspondence is not a "
                        "partition of V's weights: %s vs %s"
                        % (sorted(claimed), sorted(v_weights)))
    if len(claimed) != len(set(claimed)):
        problems.append("a valuation subfactor is claimed by two factors")
    if sorted(V_FACTOR_SUBFACTORS) != sorted(factors_in(BLOCK_V)):
        problems.append("every V factor needs a declared subfactor mapping")

    # ---- the whole model closes to COMPANY_SCALE_V2 ----------------------
    total = 0.0
    for block in BLOCKS:
        for key, w in within_block_weights(block).items():
            total += block_weight(block) * w
    scale = pit_score_signature.COMPANY_SCALE_V2
    if abs(total - scale) > 1e-12:
        problems.append("effective weights sum to %.6f, not COMPANY_SCALE_V2 "
                        "%.2f" % (total, scale))

    # the published calibration must survive adoption
    for key, want in (("ebitda_benchmark", 0.1225), ("ebitda_growth", 0.0875),
                      ("ebitda_efficiency", 0.0700),
                      ("ebitda_acceleration", 0.0700)):
        got = effective_company_weight(key)
        if abs(got - want) > 1e-12:
            problems.append("%s effective weight %.4f != the published %.4f"
                            % (key, got, want))

    # ---- Q must not become a disguised leverage block --------------------
    q = within_block_weights(BLOCK_Q)
    leverage = q["net_debt_ebitda"] + q["debt_market_cap"]
    if leverage >= 0.50:
        problems.append("leverage carries %.2f of Q; the split exists to stop "
                        "Q becoming a disguised leverage block" % leverage)
    if q["fcf_conversion"] != max(q.values()):
        problems.append("fcf_conversion must lead Q: it is the only factor "
                        "that asks whether earnings become cash")
    if q["debt_market_cap"] != min(q.values()):
        problems.append("debt_market_cap must be the smallest weight in Q: it "
                        "imports price movement into a balance-sheet block")

    # ---- within-block renormalisation IS allowed -------------------------
    one_of_four = block_score(BLOCK_Q, {"fcf_conversion": 40.0})
    if one_of_four["score"] is None or abs(one_of_four["score"] - 40.0) > 1e-12:
        problems.append("within-block renormalisation must stand in for a "
                        "missing sibling measurement")
    if abs(one_of_four["within_block_coverage"] - 0.35) > 1e-12:
        problems.append("the within-block coverage must be reported so a thin "
                        "block is distinguishable from a full one")

    # ---- G's single-point dependency is real and declared ----------------
    if len(within_block_weights(BLOCK_G)) != 1:
        problems.append("G carries exactly one factor in this version")
    if block_score(BLOCK_G, {"real_revenue_growth": None})["available"]:
        problems.append("G must be UNAVAILABLE when its one factor is")
    if "single-point" not in WITHIN_BLOCK_REASON[BLOCK_G].lower():
        problems.append("G's single-point dependency must be declared, not "
                        "discovered later")

    # ---- direction: every scoring factor, no defaults, owner signs pinned --
    for key in FACTOR_BLOCK:
        if FACTOR_DIRECTION_V1.get(key, UNDECLARED) == UNDECLARED:
            problems.append("%s has NO declared direction; a wrong sign inverts "
                            "a factor silently" % key)
        if key not in DIRECTION_SOURCE:
            problems.append("%s's direction has no recorded source" % key)
    for key, want in (("ev_ebitda", LOWER_IS_BETTER),
                      ("fcf_conversion", HIGHER_IS_BETTER),
                      ("interest_coverage", HIGHER_IS_BETTER),
                      ("net_debt_ebitda", LOWER_IS_BETTER),
                      ("debt_market_cap", LOWER_IS_BETTER)):
        if FACTOR_DIRECTION_V1.get(key) != want:
            problems.append("%s must be %s -- owner decision 2026-09-22"
                            % (key, want))
    if FACTOR_DIRECTION_V1.get("pe_ratio") != EMBEDDED_IN_TRANSFORM:
        problems.append("pe_ratio's sign is embedded in the convex transform; "
                        "orienting it again would double-apply")
    if SUBFACTOR_DIRECTION_V1.get("ev_ebitda_supplement") != LOWER_IS_BETTER:
        problems.append("ev_ebitda_supplement must be LOWER_IS_BETTER")
    for sub in ("pe_absolute", "pe_relative", "ev_ebitda_supplement"):
        if sub not in SUBFACTOR_DIRECTION_V1:
            problems.append("valuation subfactor %s has no direction" % sub)
    try:
        direction_of("some_undeclared_factor")
    except FactorMapError:
        pass
    else:
        problems.append("direction_of() must RAISE on an undeclared factor")
    if "AFTER domain-validity" not in DIRECTION_IS_ORIENTATION_ONLY:
        problems.append("the orientation-only rule must say direction follows "
                        "validity checks")
    return problems


def report() -> str:
    lines = ["THE v2 FACTOR-TO-BLOCK MAP -- %s" % BLOCK_MAP_VERSION, "",
             "  CompanyScore = 0.35 E + 0.25 V + 0.15 G + 0.10 Q", ""]
    for b in BLOCKS:
        lines.append("  %s  %-20s  w=%.2f   %s"
                     % (pit_score_signature.V2_BLOCK_LETTER[b],
                        pit_score_signature.V2_BLOCK_LABEL[b],
                        block_weight(b), BLOCK_QUESTION[b]))
        for key in factors_in(b):
            lines.append("       - %s" % key)
        lines.append("")
    lines.append("  REFUSED, pending review (not assigned for want of a home):")
    for key, rec in sorted(UNMAPPED.items()):
        lines.append("       - %-8s %s" % (key, rec["question"]))
    lines.append("")
    lines.append("  MIN_BLOCK_WEIGHT = %.2f   largest single block = %.2f"
                 % (MIN_BLOCK_WEIGHT,
                    max(pit_score_signature.V2_MAJOR_WEIGHTS.values())))
    lines.append("")
    lines.append("  every reachable combination:")
    lines.append("    %-22s %8s  %8s   %s"
                 % ("blocks available", "weight", "coverage", "verdict"))
    lines.append("    " + "-" * 60)
    for mask in range(16):
        avail = [b for i, b in enumerate(BLOCKS) if mask & (1 << (3 - i))]
        w = available_block_weight(avail)
        letters = "".join(pit_score_signature.V2_BLOCK_LETTER[b]
                          for b in avail) or "-"
        verdict = "score" if meets_min_block_weight(avail) else "REFUSED"
        lines.append("    %-22s %8.2f  %7.2f%%   %s"
                     % (letters, w, 100.0 * block_coverage(avail), verdict))
    return "\n".join(lines)


def main() -> int:
    print(report())
    print()
    problems = validate()
    print("%d problems / %s" % (len(problems), "FAIL" if problems else "PASS"))
    for p in problems:
        print("   - %s" % p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
