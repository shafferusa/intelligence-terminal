"""pit_coverage_contract -- a naked percentage is not a coverage claim.

OWNER INVARIANT, 2026-09-21. Generalised from the share-coverage reconciliation,
which found that the two figures in circulation differed by ten points while
sharing the SAME MEASURED NUMERATOR -- 4,410 peers at 2015-06-30 -- over two
different denominators. Nothing about share data separated them. One of them was
not a share statistic at all: 98.4% of the fall to 25.35% was a `dei:TradingSymbol`
gate, a pre-2019 metadata artefact wearing a share-count label.

THE CONTRACT
============

                  eligible observations with a usable value
    Coverage  =  -------------------------------------------
                    explicitly defined eligible population

BOTH halves must be declared. A percentage that names neither is not a weak
coverage claim; it is not a coverage claim. `CoverageClaim` refuses to exist
without both, so the rule is enforced at construction rather than at review.

WHY THE DENOMINATOR IS THE DANGEROUS HALF
=========================================

Measured, from the share reconciliation: holding the numerator FIXED at 272,587
and changing only the denominator moves the published figure across

    peer entity-dates          1,238,663    ->  22.01%
    priced entity-dates          377,304    ->  72.25%
    entity-dates with a count    297,868    ->  91.51%

69.51 points of spread from a choice that asks no question about share data at
all -- against 18.54 points for the staleness rule, 1.84 for the guard and 24.14
for the entire ceiling range. The denominator moved the number by more than
every rule about the data combined.

A second failure this catches: a denominator that silently changes UNIT.
`scored_universe_as_of` returns one row per LISTING (2,065 / 2,455 / 2,475) and
one per ENTITY (2,034 / 2,423 / 2,443). Two modules quoted different sides of
that into ratios. Small, and a unit error rather than a rounding one, so
`denominator_unit` is a required field here and "rows" is refused by name.

WHAT THIS MODULE DOES NOT DO
============================

It does not ban percentages, and it does not claim the registry is complete.
`audit()` scans the project for percentages sitting in coverage-shaped prose and
reports what it cannot match to a registered claim -- as CANDIDATES, with the
false-positive rate stated, exactly as `pit_share_coverage.audit_sources` does.
An auditor that claimed completeness would be making the same kind of undefended
claim this module exists to refuse.

Nothing here re-types a number owned elsewhere. Every registered claim ADAPTS
the module that measured it, so a figure cannot drift between its owner and this
registry -- there is only one copy.

Stdlib only. Reads no database.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pit_price_basis
import pit_share_coverage
import pit_valuation_spec

__all__ = [
    "CONTRACT_VERSION", "CONTRACT", "UNKNOWN",
    "UNIT_ENTITIES", "UNIT_ENTITY_DATES", "UNIT_FILINGS", "UNIT_OBSERVATIONS",
    "UNIT_COHORT_DATES", "ADMISSIBLE_UNITS", "REFUSED_UNITS",
    "NakedPercentage", "UndefinedDenominator",
    "CoverageClaim", "CLAIMS", "claim", "register", "quote", "assert_defined",
    "DENOMINATOR_SENSITIVITY", "UNIT_DEFECT",
    "audit", "AUDIT_EXEMPT", "report", "validate",
]

CONTRACT_VERSION = "coverage_contract_v1"

CONTRACT = (
    "Coverage = (eligible observations with a usable value) / (explicitly "
    "defined eligible population). Both halves are REQUIRED. A percentage "
    "published without both definitions is invalid -- not weak, invalid."
)

#: An unmeasured quantity is written as None and never as 0.
UNKNOWN = None


# ==========================================================================
# UNITS -- because a denominator that changes unit is not the same denominator
# ==========================================================================

UNIT_ENTITIES = "entities"
UNIT_ENTITY_DATES = "entity_dates"
UNIT_FILINGS = "filings"
UNIT_OBSERVATIONS = "observations"
UNIT_COHORT_DATES = "cohort_dates"

ADMISSIBLE_UNITS: tuple[str, ...] = (
    UNIT_ENTITIES, UNIT_ENTITY_DATES, UNIT_FILINGS, UNIT_OBSERVATIONS,
    UNIT_COHORT_DATES,
)

#: Units that are REFUSED by name, with the reason. `listing_rows` is refused
#: because an issuer with two listings is counted twice on one side of a ratio
#: and once on the other -- measured: 2,065 rows against 2,034 entities at the
#: 2015 census date, and the two were quoted into different published figures.
REFUSED_UNITS: dict[str, str] = {
    "listing_rows": (
        "one row per LISTING, not per issuer. `scored_universe_as_of` returns "
        "2,065 / 2,455 / 2,475 rows against 2,034 / 2,423 / 2,443 entities, and "
        "two modules quoted different sides of that into ratios. Count "
        "entities, or say entity_dates and mean it."),
    "rows": "too vague to be a unit. Rows of what?",
    "records": "too vague to be a unit. Records of what?",
}


class NakedPercentage(ValueError):
    """A coverage figure was published without its two definitions."""


class UndefinedDenominator(ValueError):
    """A denominator was named in a unit this project refuses."""


# ==========================================================================
# THE CLAIM
# ==========================================================================

@dataclass(frozen=True)
class CoverageClaim:
    """One coverage figure, with BOTH halves of the contract declared.

    Construction FAILS without both definitions. That is the whole mechanism:
    the rule is not a review checklist, it is a constructor precondition, so an
    undefined figure cannot reach the registry to be quoted from.

    `pct` is computed from the counts where they exist, never stored, so a
    published percentage that does not follow from its own counts fails
    `consistent()` rather than standing.
    """

    claim_id: str
    label: str

    #: THE TWO REQUIRED HALVES.
    numerator_definition: str
    denominator_definition: str

    denominator_unit: str
    owner: str                       # module:constant where the number LIVES
    sample_scope: str

    numerator: Optional[int] = None
    denominator: Optional[int] = None
    #: The figure as it circulates, when the counts are not both known -- a
    #: series or a band cannot be checked against one pair of counts.
    published_pct: Optional[float] = None
    measured_on: Optional[str] = None
    derived_numerator: bool = False
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("numerator_definition", "denominator_definition"):
            value = getattr(self, name)
            if not value or not value.strip():
                raise NakedPercentage(
                    "claim %r has no %s. %s"
                    % (self.claim_id, name, CONTRACT))
            if len(value.split()) < 3:
                raise NakedPercentage(
                    "claim %r's %s is %r -- a label, not a definition. Say "
                    "which observations qualify and by what rule."
                    % (self.claim_id, name, value))
        if self.denominator_unit in REFUSED_UNITS:
            raise UndefinedDenominator(
                "claim %r uses the refused unit %r: %s"
                % (self.claim_id, self.denominator_unit,
                   REFUSED_UNITS[self.denominator_unit]))
        if self.denominator_unit not in ADMISSIBLE_UNITS:
            raise UndefinedDenominator(
                "claim %r uses unit %r, which is not in ADMISSIBLE_UNITS (%s). "
                "Add it deliberately or use one of them."
                % (self.claim_id, self.denominator_unit,
                   ", ".join(ADMISSIBLE_UNITS)))
        if self.numerator is None and self.published_pct is None:
            raise NakedPercentage(
                "claim %r has neither a counted numerator nor a published "
                "percentage, so there is nothing to check it against"
                % (self.claim_id,))

    @property
    def pct(self) -> Optional[float]:
        if not self.numerator or not self.denominator:
            return UNKNOWN
        return round(100.0 * self.numerator / self.denominator, 2)

    def consistent(self, tolerance: float = 0.05) -> bool:
        """Does the published figure follow from this claim's own counts?"""
        if self.pct is None or self.published_pct is None:
            return True              # nothing to contradict
        return abs(self.pct - self.published_pct) <= tolerance

    def value(self) -> Optional[float]:
        """The figure to quote: counted where possible, published otherwise."""
        return self.pct if self.pct is not None else self.published_pct

    def render(self) -> str:
        """The ONLY supported way to put this figure into prose."""
        pct = self.value()
        head = ("UNKNOWN" if pct is None else "%.2f%%" % pct)
        lines = [
            "%s = %s   [%s]" % (self.claim_id, head, self.sample_scope),
            "  numerator    %s" % self.numerator_definition,
            "  denominator  %s  (%s)" % (self.denominator_definition,
                                         self.denominator_unit),
        ]
        if self.numerator is not None and self.denominator is not None:
            lines.append("  counts       %s / %s"
                         % (format(self.numerator, ","),
                            format(self.denominator, ",")))
        if self.derived_numerator:
            lines.append("  CAUTION      numerator BACK-DERIVED from a "
                         "published percentage, not counted")
        lines.append("  owner        %s" % self.owner)
        if self.measured_on:
            lines.append("  measured     %s" % self.measured_on)
        for note in self.notes:
            lines.append("  note         %s" % note)
        return "\n".join(lines)

    def as_dict(self) -> dict[str, Any]:
        out = dict(self.__dict__)
        out["pct_from_counts"] = self.pct
        out["value"] = self.value()
        out["consistent"] = self.consistent()
        out["contract_version"] = CONTRACT_VERSION
        return out


CLAIMS: dict[str, CoverageClaim] = {}


def register(c: CoverageClaim) -> CoverageClaim:
    """Add a claim, refusing a second definition of the same id."""
    if c.claim_id in CLAIMS:
        raise ValueError(
            "claim id %r is already registered. Two definitions of one figure "
            "is the condition this module exists to end." % (c.claim_id,))
    CLAIMS[c.claim_id] = c
    return c


def claim(claim_id: str) -> CoverageClaim:
    if claim_id not in CLAIMS:
        raise KeyError(
            "no registered coverage claim %r. Register it with both halves of "
            "the contract, or do not publish the number." % (claim_id,))
    return CLAIMS[claim_id]


def quote(claim_id: str) -> str:
    """Render a registered claim. The only supported path into prose."""
    return claim(claim_id).render()


# ==========================================================================
# ADAPTERS -- every number stays owned by the module that measured it
# ==========================================================================

def _adopt_share_coverage() -> None:
    """Adapt `pit_share_coverage.DEFINITIONS`. No figure is re-typed here."""
    for spec_id, d in sorted(pit_share_coverage.DEFINITIONS.items()):
        unit = d.denominator_unit
        # The share module records the defect honestly under its own name; the
        # contract refuses that unit, so the claim is registered under the unit
        # it SHOULD have and the defect travels as a note.
        note_extra: tuple[str, ...] = ()
        if unit in REFUSED_UNITS:
            note_extra = ("denominator unit %r is REFUSED by the contract: %s"
                          % (unit, REFUSED_UNITS[unit]),)
            unit = UNIT_ENTITIES
        register(CoverageClaim(
            claim_id="share." + spec_id,
            label=d.label,
            numerator_definition=d.numerator_rule,
            denominator_definition=d.denominator_name,
            denominator_unit=unit,
            owner="pit_share_coverage:DEFINITIONS[%r]" % spec_id,
            sample_scope=d.sample_scope,
            numerator=d.numerator,
            denominator=d.denominator,
            published_pct=d.anchor_pct,
            measured_on=pit_share_coverage.MEASURED_ON,
            derived_numerator=d.derived_numerator,
            notes=tuple(d.notes) + note_extra,
        ))


def _adopt_eps_pair_paths() -> None:
    """Adapt `pit_price_basis.PAIR_PATH_COVERAGE`."""
    cov = pit_price_basis.PAIR_PATH_COVERAGE
    register(CoverageClaim(
        claim_id="eps.same_filing_pair.filing_groups",
        label="filing groups carrying a same-filing EPS comparative",
        numerator_definition=(
            "(entity, accn, qtrs) groups in pit_eps_obs presenting two or more "
            "DISTINCT period_end values, i.e. a comparative pair the issuer "
            "already restated onto one share basis"),
        denominator_definition=(
            "all (entity, accn, qtrs) groups in pit_eps_obs, whole store, "
            "with no availability or as-of restriction applied"),
        denominator_unit=UNIT_FILINGS,
        owner="pit_price_basis:PAIR_PATH_COVERAGE",
        sample_scope=cov["scope"],
        numerator=cov["filing_groups_with_comparative"],
        denominator=cov["filing_groups_entity_accn_qtrs"],
        published_pct=cov["filing_groups_with_comparative_pct"],
        measured_on=cov["measured_on"],
        notes=(cov["caveat"],),
    ))
    register(CoverageClaim(
        claim_id="eps.same_filing_pair.annual_filings",
        label="annual filings carrying a comparative period",
        numerator_definition=(
            "qtrs=4 (entity, accn) groups in pit_eps_obs presenting two or "
            "more distinct period_end values"),
        denominator_definition=(
            "all qtrs=4 (entity, accn) groups in pit_eps_obs, whole store"),
        denominator_unit=UNIT_FILINGS,
        owner="pit_price_basis:PAIR_PATH_COVERAGE",
        sample_scope=cov["scope"],
        numerator=cov["annual_filings_with_comparative"],
        denominator=cov["annual_filings_qtrs4"],
        published_pct=cov["annual_filings_with_comparative_pct"],
        measured_on=cov["measured_on"],
        notes=(cov["caveat"],),
    ))
    register(CoverageClaim(
        claim_id="eps.same_filing_pair.entities",
        label="entities with at least one same-filing annual EPS pair",
        numerator_definition=(
            "entities having at least ONE qtrs=4 accession that presents two "
            "or more distinct period_end values -- an entity-level EXISTENCE "
            "claim, NOT a claim that every date is covered"),
        denominator_definition=(
            "entities with any qtrs=4 EPS row in pit_eps_obs"),
        denominator_unit=UNIT_ENTITIES,
        owner="pit_price_basis:PAIR_PATH_COVERAGE",
        sample_scope=cov["scope"],
        numerator=cov["entities_with_same_filing_pair"],
        denominator=cov["entities_with_annual_eps"],
        published_pct=cov["entities_with_same_filing_pair_pct"],
        measured_on=cov["measured_on"],
        notes=(cov["caveat"],
               "AN EXISTENCE CLAIM IS NOT A COVERAGE CLAIM ABOUT DATES. 99.34% "
               "of entities have at least one such pair; the share of "
               "ENTITY-DATES with a usable pair is a different number and is "
               "UNKNOWN."),
    ))


def _adopt_valuation_cohorts() -> None:
    """Adapt the cohort-sufficiency rates from `pit_valuation_spec.MEASURED`."""
    m = pit_valuation_spec.MEASURED
    scope = m["sample_scope"]["cohorts_and_coverage"]
    for key, cohort_label, pct_key, rung in (
            ("ev_ebitda_cohort", "EV/EBITDA peer cohort", "pct_ge_3", "any"),
            ("ev_ebitda_cohort", "EV/EBITDA peer cohort", "sic4_pct_ge_12",
             "sic4"),
            ("pe_cohort", "P/E peer cohort", "pct_ge_3", "any"),
            ("pe_cohort", "P/E peer cohort", "sic4_pct_ge_12", "sic4"),
            ("ebitda_only_cohort", "EBITDA-only benchmark cohort", "pct_ge_3",
             "any"),
    ):
        block = m.get(key)
        if not block or pct_key not in block:
            continue
        threshold = "3" if "ge_3" in pct_key else "12"
        register(CoverageClaim(
            claim_id="cohort.%s.%s.n_ge_%s" % (key, rung, threshold),
            label="%s reaching %s+ members (%s rung)"
                  % (cohort_label, threshold, rung),
            numerator_definition=(
                "entity-dates whose %s contains at least %s members at the %s "
                "rung, after the peer-set eligibility gates"
                % (cohort_label, threshold, rung)),
            denominator_definition=(
                "entity-dates on the month-end as-of grid for which a peer set "
                "of this kind was constructed at all"),
            denominator_unit=UNIT_COHORT_DATES,
            owner="pit_valuation_spec:MEASURED[%r][%r]" % (key, pct_key),
            sample_scope=scope,
            published_pct=float(block[pct_key]),
            measured_on=m["measured_on"],
            notes=("counts not recorded alongside the rate in the owning "
                   "module; the rate is the published figure and the counts "
                   "are UNKNOWN here rather than invented",),
        ))


_adopt_share_coverage()
_adopt_eps_pair_paths()
_adopt_valuation_cohorts()


# ==========================================================================
# THE TWO MEASURED LESSONS, kept beside the rule they justify
# ==========================================================================

#: Hold the numerator FIXED and move only the denominator. From the share
#: reconciliation; adapted, not re-typed.
def DENOMINATOR_SENSITIVITY() -> dict[str, Any]:
    """The 69.51-point spread that motivates the contract."""
    return {
        "numerator_held_at": 272587,
        "numerator_definition": (
            "entity-dates with a defensible point-in-time share STATE under "
            "the v2 staleness rule"),
        "denominators": {
            "peer entity-dates": 1238663,
            "priced entity-dates": 377304,
            "entity-dates with any in-scope count": 297868,
        },
        "spread_points": 69.51,
        "against": {
            "staleness rule": 18.54,
            "entire ceiling range": 24.14,
            "share-class guard": 1.84,
        },
        "reading": (
            "The denominator moved the published figure by more than every "
            "rule about the DATA combined. A percentage whose denominator is "
            "unstated is therefore not a weak claim; it is not a claim."),
        "owner": "pit_share_coverage:denominator_sensitivity",
    }


UNIT_DEFECT: dict[str, Any] = {
    "what": "a denominator that silently changed UNIT",
    "measured": {
        "scored_universe_as_of rows": (2065, 2455, 2475),
        "scored_universe_as_of entities": (2034, 2423, 2443),
    },
    "consequence": (
        "an issuer with two listings is counted twice on one side of a ratio "
        "and once on the other. Small -- 31/32/32 -- and a UNIT error rather "
        "than a rounding one, which is why `listing_rows` is refused by name "
        "rather than tolerated with a caveat."),
    "owner": "pit_share_coverage:MEASURED",
}


# ==========================================================================
# THE AUDITOR -- candidates, never a completeness claim
# ==========================================================================

#: Words whose presence near a percentage makes it coverage-shaped prose.
_COVERAGE_WORDS = (
    "coverage", "covered", "resolvable", "available", "availability",
    "share of", "of the universe", "of entities", "of the cross-section",
    "of observations", "of filings", "of peers", "clears twelve",
)

_PCT_RE = re.compile(r"(?<![\w.])(\d{1,3}(?:\.\d{1,2})?)\s*%")

#: Files that are ALLOWED to carry bare percentages, with the reason. Not a
#: silencer: each entry is a deliberate exemption a reader can challenge.
AUDIT_EXEMPT: dict[str, str] = {
    "pit_coverage_contract.py": "this module -- it quotes figures to define them",
    "pit_share_coverage.py": "owns the share figures and enforces its own rule",
    "test_pit_coverage_contract.py": "asserts the rule, so it must quote it",
}


def _registered_values() -> set[str]:
    out: set[str] = set()
    for c in CLAIMS.values():
        for v in (c.pct, c.published_pct):
            if v is not None:
                out.add("%.2f" % v)
                out.add("%g" % v)
    return out


def audit(root: Optional[str] = None) -> dict[str, Any]:
    """Scan for coverage-shaped percentages that match no registered claim.

    Returns CANDIDATES. It cannot and does not claim to find every undefined
    coverage figure in the project, and it will flag percentages that are not
    coverage claims at all -- a share of a distribution, a tolerance, a
    measured spread. The output says so, because an auditor that implied
    completeness would be making exactly the undefended claim this module
    refuses.
    """
    root = root or os.path.dirname(os.path.abspath(__file__))
    known = _registered_values()
    hits: list[dict[str, Any]] = []
    scanned = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in (".git", "__pycache__", ".venv", "node_modules")]
        for fn in sorted(filenames):
            if not fn.endswith((".py", ".md")):
                continue
            if fn in AUDIT_EXEMPT:
                continue
            path = os.path.join(dirpath, fn)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    lines = fh.read().splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            scanned += 1
            for i, line in enumerate(lines, 1):
                low = line.lower()
                if not any(w in low for w in _COVERAGE_WORDS):
                    continue
                for raw in _PCT_RE.findall(line):
                    if raw in known or ("%.2f" % float(raw)) in known:
                        continue
                    hits.append({
                        "file": os.path.relpath(path, root),
                        "line": i,
                        "pct": raw,
                        "text": line.strip()[:140],
                    })
    return {
        "contract_version": CONTRACT_VERSION,
        "files_scanned": scanned,
        "registered_claims": len(CLAIMS),
        "candidates": hits,
        "candidate_count": len(hits),
        "status": "CANDIDATES, NOT A COMPLETENESS CLAIM",
        "false_positives_expected": (
            "This matches on prose shape, so it will flag percentages that are "
            "not coverage claims -- prevalence rates, measured spreads, "
            "tolerances and quoted context. Triage before acting; do not "
            "rewrite a figure that is stamped into store provenance."),
        "exempt": dict(AUDIT_EXEMPT),
    }


def assert_defined(text: str) -> None:
    """Refuse a text that quotes a REGISTERED figure without naming its claim.

    The generalisation of `pit_share_coverage.assert_quotable`. It is not a ban
    on percentages: it bans quoting a figure this project has defined without
    saying which definition is meant.
    """
    offenders: list[str] = []
    for raw in _PCT_RE.findall(text):
        for c in CLAIMS.values():
            v = c.value()
            if v is None or abs(v - float(raw)) > 0.005:
                continue
            if c.claim_id in text or c.owner.split(":")[-1] in text:
                continue
            offenders.append("%s%% (%s)" % (raw, c.claim_id))
    if offenders:
        raise NakedPercentage(
            "registered coverage figures quoted without their claim ids: "
            + "; ".join(sorted(set(offenders)))
            + ". Use pit_coverage_contract.quote(claim_id).")


# ==========================================================================
# REPORT AND SELF-CHECK
# ==========================================================================

def report() -> str:
    lines = ["THE COVERAGE CONTRACT -- %s" % CONTRACT_VERSION, "",
             CONTRACT, "",
             "REGISTERED CLAIMS (%d)" % len(CLAIMS), ""]
    for cid in sorted(CLAIMS):
        lines.append(CLAIMS[cid].render())
        lines.append("")
    ds = DENOMINATOR_SENSITIVITY()
    lines += ["THE MEASUREMENT THAT MOTIVATES THE RULE", "",
              "  numerator held at %s" % format(ds["numerator_held_at"], ",")]
    for name, den in ds["denominators"].items():
        lines.append("    %-38s %13s  ->  %6.2f%%"
                     % (name, format(den, ","),
                        100.0 * ds["numerator_held_at"] / den))
    lines.append("")
    lines.append("  spread %.2f points, against %.2f for the staleness rule, "
                 "%.2f for the whole ceiling range and %.2f for the guard."
                 % (ds["spread_points"], ds["against"]["staleness rule"],
                    ds["against"]["entire ceiling range"],
                    ds["against"]["share-class guard"]))
    return "\n".join(lines)


def validate() -> list[str]:
    problems: list[str] = []

    if not CLAIMS:
        problems.append("no claims registered; the adapters did not run")

    for cid, c in sorted(CLAIMS.items()):
        if not c.consistent():
            problems.append(
                "%s: published %.2f%% does not follow from its own counts "
                "(%.2f%%)" % (cid, c.published_pct, c.pct))
        if c.denominator_unit not in ADMISSIBLE_UNITS:
            problems.append("%s: inadmissible denominator unit %r"
                            % (cid, c.denominator_unit))
        if c.sample_scope in ("", None):
            problems.append("%s: no sample scope declared" % cid)

    # The constructor must refuse each way of being naked.
    def _mk(**kw):
        base = dict(claim_id="probe", label="probe",
                    numerator_definition="observations that have a usable value",
                    denominator_definition="the eligible population as defined",
                    denominator_unit=UNIT_ENTITIES, owner="probe:PROBE",
                    sample_scope="PROBE", numerator=1, denominator=2)
        base.update(kw)
        return lambda: CoverageClaim(**base)

    for name, thunk, exc in (
            ("empty numerator definition", _mk(numerator_definition=""),
             NakedPercentage),
            ("empty denominator definition", _mk(denominator_definition=" "),
             NakedPercentage),
            ("a label instead of a definition",
             _mk(denominator_definition="all entities"), NakedPercentage),
            ("the refused listing_rows unit",
             _mk(denominator_unit="listing_rows"), UndefinedDenominator),
            ("an unknown unit", _mk(denominator_unit="thingies"),
             UndefinedDenominator),
            ("no counts and no published percentage",
             _mk(numerator=None, denominator=None, published_pct=None),
             NakedPercentage),
    ):
        try:
            thunk()
        except exc:
            pass
        except Exception as exc_actual:          # pragma: no cover
            problems.append("%s raised %r instead of %s"
                            % (name, exc_actual, exc.__name__))
        else:
            problems.append("%s was ACCEPTED; the contract is not enforced"
                            % name)

    # A duplicate id must be refused.
    try:
        register(CLAIMS[sorted(CLAIMS)[0]])
    except ValueError:
        pass
    else:
        problems.append("a duplicate claim id was accepted")

    # The motivating measurement must still reproduce.
    ds = DENOMINATOR_SENSITIVITY()
    pcts = [100.0 * ds["numerator_held_at"] / d
            for d in ds["denominators"].values()]
    if abs((max(pcts) - min(pcts)) - ds["spread_points"]) > 0.02:
        problems.append("the denominator spread drifted: recomputed %.2f, "
                        "published %.2f" % (max(pcts) - min(pcts),
                                            ds["spread_points"]))
    if not all(ds["spread_points"] > v for v in ds["against"].values()):
        problems.append("the denominator must dominate every data rule, or "
                        "the rule's justification is overstated")

    if "listing_rows" not in REFUSED_UNITS:
        problems.append("listing_rows must be refused BY NAME")
    return problems


def main() -> int:
    print(report())
    print()
    a = audit()
    print("AUDIT -- %s" % a["status"])
    print("  %d files scanned, %d registered claims, %d candidates"
          % (a["files_scanned"], a["registered_claims"], a["candidate_count"]))
    print("  %s" % a["false_positives_expected"])
    for h in a["candidates"][:12]:
        print("    %s:%s  %s%%  %s" % (h["file"], h["line"], h["pct"],
                                       h["text"][:80]))
    if a["candidate_count"] > 12:
        print("    ... and %d more" % (a["candidate_count"] - 12))
    print()
    problems = validate()
    print("%d problems / %s" % (len(problems), "FAIL" if problems else "PASS"))
    for p in problems:
        print("   - %s" % p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
