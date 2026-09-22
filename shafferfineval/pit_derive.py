"""The derivation graph: what is a SOURCE FACT, what is a SHAFFER INVENTION.

This module exists to correct a category error that has been running through
the coverage work. The project has been reporting derived quantities as
"unavailable" because no data provider publishes them. No provider publishes

    "mean EBITDA of companies in the 50th-75th EBITDA percentile of this
     company's sector as of 2018-06-30"

and none ever will. That number is the PRODUCT. Calling it unavailable is like
a kitchen reporting that the wholesaler does not stock coq au vin.

    The database supplies ingredients. ShafferFinEval cooks the meal.

So every quantity in the equity model is classified into exactly one of three
buckets, and the bucket is a property of the NODE, recorded here as data:

  SOURCE_PRIMITIVE      A fact obtained from a source. Revenue, operating
                        income, D&A, net income, debt, cash, share counts,
                        historical price, SIC, CPI, Treasury yields. If it is
                        missing, some filer did not tag it or some vendor does
                        not carry it, and no amount of arithmetic repairs that.

  SHAFFER_DERIVED       Everything we calculate. EBITDA, margins, growth,
                        acceleration, sector percentiles, the 50-75 cohort and
                        its means, the benchmark gap, EV/EBITDA, the valuation
                        penalty, CompanyScore, SectorScore, ShafferScore. A
                        derived node is NEVER "unavailable" as a metric. It
                        either resolves, or a NAMED PRIMITIVE underneath it did
                        not resolve, and the honest report says WHICH.

  GENUINELY_UNAVAILABLE The underlying PRIMITIVE cannot be obtained from any
                        source. Four cases today, enumerated in
                        UNAVAILABLE_REGISTER, and it is a much smaller category
                        than this project has been implying.

What this module therefore replaces is every sentence of the form
"feature X unavailable". In its place:

    ev_ebitda -- derivation failure at 2024-06-28
      ebitda*                 51.3%
      price_adjusted          41.6%
      shares_outstanding      41.4%
      total_debt              37.7%
      cash                    95.4%
      all primitives jointly   3.2% (independence) .. 37.7% (Frechet upper)
      BINDING LEAF: total_debt

A binding leaf is actionable; "unavailable" is not.

THIS MODULE IS DECLARATIVE. It describes and inspects the computation. It does
not perform the replay, it reads nothing, and it writes nothing -- there is no
`sqlite3.connect` in it, no network call, and `ensure_schema` is the only
function that touches a connection, is never called from here, and is written
for ANOTHER process to run later. `pit_feature` and `pit_score` stay empty.

Two joint-availability models are reported side by side, and the reason is
that neither one is the truth:

  INDEPENDENCE (the product)  A convenient fiction. Company facts CO-OCCUR: a
                              filer that tags one balance-sheet item usually
                              tags the neighbouring ones, so the product is a
                              LOWER bound in practice, not an estimate.
  FRECHET BOUNDS              max(0, sum - (n-1)) <= joint <= min(marginals).
                              Assumption-free and often uselessly wide, but it
                              is the ceiling, and the ceiling is the scarcest
                              marginal -- which is the binding leaf again.

Where a true measured joint exists we use it and show how far the estimates
were off. EBITDA is the calibration point: operating income 70.1% and D&A
61.4% at 2015-06-30 multiply to 43.0%, the Frechet bracket is [31.5%, 61.4%],
and the MEASURED joint is 48.1%. Independence understates by 5.1 points. That
co-occurrence lift (1.118, 1.072, 1.047 at the three dates) is itself a
result, and `coverage` carries it forward as a third, explicitly weakest,
estimate for nodes whose joint has never been measured.

Stdlib only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

__all__ = [
    # buckets and scopes
    "KIND_SOURCE", "KIND_DERIVED", "KIND_UNAVAILABLE", "KINDS",
    "SCOPE_COMPANY", "SCOPE_COHORT", "SCOPE_MACRO",
    "COMBINE_ALL", "COMBINE_ANY",
    # graph
    "Input", "Node", "NODES", "node", "node_keys", "nodes_of_kind",
    "primitives", "derived", "cohort_nodes", "topological_order",
    "is_acyclic", "ancestors", "descendants", "between", "leaves",
    "validate_graph",
    # measured inputs
    "MEASURED_COVERAGE", "MEASURED_DATES", "MEASURED_JOINTS",
    "primitive_availability", "co_occurrence_lift", "PEER_SET_MEAN_SIZE",
    "COHORT_BAND_FRACTION", "MIN_EBITDA_COHORT",
    # coverage
    "Estimate", "frechet_bounds", "independence_joint", "union_joint",
    "binomial_at_least", "cohort_formation_probability",
    "coverage", "coverage_report", "REPORT_NODES",
    # classification
    "UNAVAILABLE_REGISTER", "classify", "unavailable_admission_test",
    # trace
    "TraceStep", "trace", "render_trace", "FIXTURE_AS_OF", "FIXTURE_VALUES",
    "TRACE_EXTRAS", "OWNER_TRACE_NODES", "FIXTURE_NOTE",
    "NEXT_MEASUREMENT",
    # intermediate storage -- specified, NOT applied
    "PIT_INTERMEDIATE_DDL", "ensure_schema", "storage_recommendation",
]


# ==========================================================================
# (0) THE THREE BUCKETS
# ==========================================================================

#: A fact we must obtain from a source. Absence is a sourcing problem.
KIND_SOURCE = "SOURCE_PRIMITIVE"

#: A quantity we calculate. Absence is ALWAYS a named primitive's absence.
KIND_DERIVED = "SHAFFER_DERIVED"

#: A primitive that cannot be obtained from any source. See UNAVAILABLE_REGISTER.
KIND_UNAVAILABLE = "GENUINELY_UNAVAILABLE"

KINDS = (KIND_SOURCE, KIND_DERIVED, KIND_UNAVAILABLE)

#: Company-level: one value per (entity, as_of).
SCOPE_COMPANY = "company"

#: Cohort-level: ONE value per (sector rung, key, as_of), consumed by every
#: member of that cohort. The percentiles, the 50-75 membership band and the
#: cohort means are all of this kind, and the distinction is the whole reason
#: section 5 of this module exists -- a per-company intermediates table would
#: store each of these numbers ~32 times over.
SCOPE_COHORT = "cohort"

#: Economy-level: one value per as_of, shared by everything. CPI, Treasury.
SCOPE_MACRO = "macro"

#: Every listed input is required; the node resolves only if all of them do.
COMBINE_ALL = "all"

#: The node renormalises over whatever resolved and needs only `min_inputs` of
#: them. The production core does this at four places (the three factor blends
#: and the major-weight blend), and a coverage model that treated those as
#: conjunctions would understate the score's availability badly.
COMBINE_ANY = "any"


# ==========================================================================
# (1) THE NODE REGISTRY
# ==========================================================================

@dataclass(frozen=True)
class Input:
    """One edge of the derivation graph.

    `lag` is in YEARS and is load-bearing rather than decorative: EBITDA growth
    does not need EBITDA, it needs EBITDA at two dates, and EBITDA acceleration
    needs it at three. A coverage model that ignored the lag would report
    acceleration as being as available as its level, which is the mistake that
    makes a growth factor look cheap.

    `per_member` marks an input consumed ONCE PER COHORT MEMBER rather than
    once. The sector EBITDA percentile does not need one EBITDA; it needs
    enough of them, and "enough" is a count threshold, not a conjunction.

    `required` is False for an input the consumer treats as optional --
    ShafferScore adds a missing sector overlay as zero rather than refusing to
    score, so the overlay must not enter the joint.

    `gate` is True for an input that is required EVEN ON A RENORMALISING NODE.
    The growth factor survives losing any one of its three components, but it
    does not survive losing the peer set: without peers there is nothing to
    rank against, and a union model that swallowed the peer set would report
    the growth factor as always available.
    """

    key: str
    lag: int = 0
    per_member: bool = False
    required: bool = True
    gate: bool = False
    note: str = ""

    @property
    def leaf_id(self) -> str:
        """Identity of this edge's endpoint as it appears in a coverage report."""
        if self.per_member:
            return f"{self.key}[per peer]"
        if self.lag:
            return f"{self.key}@t-{self.lag}y"
        return self.key


@dataclass(frozen=True)
class Node:
    """One quantity in the Shaffer equity model.

    A node carries what a reader needs in order to argue with it: which bucket
    it is in, what it depends on, the arithmetic EXACTLY as the production core
    performs it, the economic question it answers, and -- for a primitive --
    which ladder or table supplies it.

    The invariant this type exists to hold: `kind == KIND_DERIVED` implies
    `inputs` is non-empty, and `kind == KIND_SOURCE` implies it is empty. A
    derived node with no inputs would be a number with no provenance, and a
    primitive with inputs would be a computation pretending to be a fact.
    `validate_graph` enforces both.
    """

    key: str
    kind: str
    scope: str
    formula: str
    question: str
    inputs: tuple[Input, ...] = ()
    combine: str = COMBINE_ALL
    min_inputs: int = 0            # COMBINE_ANY only; 0 means "1"
    member_basis: str = ""         # cohort nodes: 'sector' | 'cohort'
    min_members: int = 0           # cohort nodes: the count threshold
    source: str = ""               # primitives: the ladder / table / series
    unit: str = "number"           # usd | ratio | pct | score | count | text
    note: str = ""

    @property
    def is_primitive(self) -> bool:
        return self.kind == KIND_SOURCE

    @property
    def required_inputs(self) -> tuple[Input, ...]:
        return tuple(i for i in self.inputs if i.required)

    @property
    def gate_inputs(self) -> tuple[Input, ...]:
        """Required whatever the combine rule says. See `Input.gate`."""
        return tuple(i for i in self.inputs if i.required and i.gate)

    @property
    def blend_inputs(self) -> tuple[Input, ...]:
        """The inputs a renormalising node may drop. Identical to
        `required_inputs` on a conjunctive node, where nothing may be dropped."""
        return tuple(i for i in self.inputs if i.required and not i.gate)

    def as_dict(self) -> dict[str, Any]:
        """The node as JSON, for a replay run to store beside its numbers."""
        out: dict[str, Any] = {
            "key": self.key,
            "kind": self.kind,
            "scope": self.scope,
            "formula": self.formula,
            "question": self.question,
            "combine": self.combine,
            "unit": self.unit,
            "inputs": [
                {"key": i.key, "lag": i.lag, "per_member": i.per_member,
                 "required": i.required, "note": i.note}
                for i in self.inputs
            ],
        }
        for name in ("min_inputs", "member_basis", "min_members", "source", "note"):
            value = getattr(self, name)
            if value:
                out[name] = value
        return out


def _p(key: str, formula: str, question: str, source: str,
       scope: str = SCOPE_COMPANY, unit: str = "usd", note: str = "") -> Node:
    """A SOURCE_PRIMITIVE. No inputs, by definition."""
    return Node(key=key, kind=KIND_SOURCE, scope=scope, formula=formula,
                question=question, source=source, unit=unit, note=note)


def _d(key: str, formula: str, question: str, inputs: Sequence[Input],
       scope: str = SCOPE_COMPANY, unit: str = "ratio",
       combine: str = COMBINE_ALL, min_inputs: int = 0,
       member_basis: str = "", min_members: int = 0, note: str = "") -> Node:
    """A SHAFFER_DERIVED node. Never reported as 'unavailable' as a metric."""
    return Node(key=key, kind=KIND_DERIVED, scope=scope, formula=formula,
                question=question, inputs=tuple(inputs), combine=combine,
                min_inputs=min_inputs, member_basis=member_basis,
                min_members=min_members, unit=unit, note=note)


def _u(key: str, question: str, source: str, note: str,
       scope: str = SCOPE_COMPANY, unit: str = "usd") -> Node:
    """A GENUINELY_UNAVAILABLE primitive. Membership is argued, not assumed."""
    return Node(key=key, kind=KIND_UNAVAILABLE, scope=scope,
                formula="(no source supplies this)", question=question,
                source=source, unit=unit, note=note)


#: Minimum members of the 50th-75th EBITDA percentile band before it may be
#: used as a benchmark. Mirrors company_scoring.MIN_EBITDA_COHORT; restated
#: rather than imported because this module must not import the frozen core.
MIN_EBITDA_COHORT = 3

#: The 50-75 band is a QUARTER of the valid peers by construction.
COHORT_BAND_FRACTION = 0.25

#: Mean members per peer set: 1,240,000 entity-dates / 39,038 peer sets.
#: A mean, not a distribution -- and the distinction matters enormously, see
#: `cohort_formation_probability`.
PEER_SET_MEAN_SIZE = 1_240_000 / 39_038          # 31.76

_SIC_SOURCE = "pit_entity_sic (SIC as known at as_of), pit_peer_set rung ladder"

NODES: dict[str, Node] = {}


def _register(*items: Node) -> None:
    for item in items:
        if item.key in NODES:
            raise ValueError(f"duplicate node {item.key!r}")
        NODES[item.key] = item


# -- 1a. SOURCE PRIMITIVES -------------------------------------------------
# Facts. Every one of these is obtained; none is computed. Absence here is a
# sourcing fact about a filer or a vendor, and it is the ONLY kind of absence
# that may ever be reported as an absence.

_register(
    _p("revenue",
       "resolve('revenue', as_of) -> pit_fact.val",
       "How much did the business sell in the period?",
       "pit_policy ladder 'revenue' (6 rungs; ASC 606 rungs gated from 2018-01-01)"),
    _p("operating_income",
       "resolve('operating_income', as_of) -> pit_fact.val",
       "What did the business earn before interest, tax and non-operating items?",
       "pit_policy ladder 'operating_income' (1 rung: OperatingIncomeLoss)",
       note="Banks and insurers largely do not tag it; a structural absence."),
    _p("depreciation_amortisation",
       "resolve('depreciation_amortisation', as_of) -> pit_fact.val",
       "How much non-cash capital consumption was charged against that income?",
       "pit_policy ladder 'depreciation_amortisation' (4 rungs, union of the cash-flow variants)",
       note="MEASURED as the scarcer half of EBITDA at all three dates."),
    _p("net_income",
       "resolve('net_income', as_of) -> pit_fact.val",
       "What was left for the owners after everything?",
       "pit_policy ladder 'net_income' (NetIncomeLoss -> ProfitLoss -> ...AvailableToCommon)"),
    _p("total_debt",
       "resolve('total_debt', as_of) -> pit_fact.val (composite rungs are sums)",
       "What does the business owe to lenders?",
       "pit_policy ladder 'total_debt'; v1 15.9/17.8/14.5%, v2 37.9/42.3/37.7% of base",
       note="Under v2 most resolved rows sit on a LOWER-BOUND rung and understate leverage."),
    _p("cash",
       "resolve('cash', as_of) -> pit_fact.val",
       "How much of the debt could be repaid tomorrow out of the till?",
       "pit_policy ladder 'cash' (carrying value -> restricted-inclusive fallback)"),
    _p("total_assets",
       "resolve('total_assets', as_of) -> pit_fact.val",
       "How big is the balance sheet the earnings were produced on?",
       "pit_policy ladder 'total_assets' (Assets)",
       note="100% by construction: the base universe IS peers with a usable Assets fact."),
    _p("equity",
       "resolve('equity', as_of) -> pit_fact.val",
       "How much of the balance sheet belongs to shareholders?",
       "pit_policy ladder 'equity' (StockholdersEquity -> including-NCI fallback)"),
    _p("operating_cash_flow",
       "resolve('operating_cash_flow', as_of) -> pit_fact.val",
       "How much cash did operations actually produce?",
       "pit_policy ladder 'operating_cash_flow'"),
    _p("capex",
       "resolve('capex', as_of) -> pit_fact.val",
       "How much cash had to be reinvested to keep the assets standing?",
       "pit_policy ladder 'capex'",
       note="Filed as a positive outflow; the sign belongs to the feature builder."),
    _p("interest_expense",
       "resolve('interest_expense', as_of) -> pit_fact.val",
       "What does the debt cost each period?",
       "pit_policy ladder 'interest_expense' (AUTHORED, not coverage-surveyed)"),
    _p("shares_outstanding",
       "resolve('shares_outstanding', as_of) -> pit_fact.val, summed across classes",
       "How many claims is the equity value divided among?",
       "pit_policy ladder 'shares_outstanding' (dei cover page -> per-class -> diluted average)",
       unit="count",
       note=("Raw ladder coverage 61.1/64.9/63.5% but DEFENSIBLE coverage after "
             "the share-class guard is 25.35/35.20/41.35%. The defensible number "
             "is the one this module uses: a per-class count taken for the whole "
             "company mis-scales market cap for the entire entity.")),
    _p("price_raw",
       "pit_price_bar.close at the last session <= as_of",
       "What did one share change hands for?",
       "pit_price_bar, keyed listing_id (never a ticker)",
       unit="usd"),
    _p("price_adjusted",
       "price_raw * PROD(split and dividend factors from pit_corporate_action)",
       "What did one of today's shares change hands for, on a comparable basis?",
       "pit_price_bar x pit_corporate_action",
       unit="usd",
       note=("Classified a PRIMITIVE although it is mechanically reconstructed: "
             "the adjustment factors are themselves sourced facts and no Shaffer "
             "judgement enters. It is a vendor-equivalent quantity, not an invention.")),
    _p("sic",
       "pit_store.sic_as_of(entity_id, as_of)",
       "Which industry was this company in ON THAT DAY?",
       _SIC_SOURCE,
       unit="text",
       note="Point-in-time: a company reclassified in 2021 was not in that sector in 2015."),
    _p("cpi",
       "pit_store.macro_value_as_of('CPIAUCSL', period, as_of)",
       "How much of a revenue increase was price rather than volume?",
       "pit_macro_obs, 248,934 rows over 24 series; complete at every grid date",
       scope=SCOPE_MACRO, unit="index"),
    _p("treasury_yield",
       "pit_curve_obs.value at tenor, last observation <= as_of",
       "What did money cost, risk-free, on that day?",
       "pit_curve_obs",
       scope=SCOPE_MACRO, unit="pct",
       note=("Registered because the model may consume it; NO equity node in this "
             "graph consumes it today, and its coverage has not been surveyed. "
             "It carries no availability figure rather than an invented one.")),
)


# -- 1b. GENUINELY UNAVAILABLE PRIMITIVES ----------------------------------
# Four, and only four. Each one is a PRIMITIVE that cannot be obtained, never
# a derived metric that no vendor happens to publish. See `classify`.

_register(
    _u("price_dead_company",
       "What did this share trade at, after every vendor stopped carrying it?",
       "no source: delisted before the vendor's history begins, or purged from it",
       note=("Entity-conditional and date-conditional, NOT a blanket state. It is "
             "genuinely unavailable only for the specific (listing, window) where "
             "pit_price_bar has no row AND no vendor carries one. An entity that "
             "merely fails to resolve a ticker is an IDENTITY problem, which is "
             "repairable and therefore does not belong in this bucket.")),
    _u("option_quote_pre_archive",
       "What was this option's bid, ask and implied volatility on a past date?",
       "no source: the option archive began 2026-09-20 and chains are not retro-sold",
       note=("Date-conditional: unavailable strictly BEFORE 2026-09-20 and "
             "available from it. An option chain is a snapshot; nobody can "
             "reconstruct a quote that was never recorded. This is the cleanest "
             "member of the bucket.")),
    _u("analyst_gpi_historical",
       "What geopolitical-impact score would an analyst have assigned on that date?",
       "no source: GPI is analyst-authored and no historical series was ever written",
       unit="score",
       note=("Not merely unpublished -- NEVER GENERATED. Back-filling it today "
             "would be authoring an opinion with hindsight and stamping a "
             "historical date on it, which is the one thing a point-in-time store "
             "exists to prevent. The honest replay drops the GPI factor and "
             "renormalises, and says it did.")),
    _u("never_tagged_fact",
       "What was the value of a concept this filer never tagged in XBRL?",
       "no source: absent from num.txt, and not legitimately reconstructible",
       note=("The largest of the four and the one most easily abused. It applies "
             "ONLY where the fact is absent from every rung of every ladder "
             "version AND cannot be derived from facts that are present. It does "
             "NOT cover a fact the current ladder misses -- v2 moved total_debt "
             "from 15.9% to 37.9% by widening the ladder, which proves those rows "
             "were never genuinely unavailable. pit_policy.absence_limits() also "
             "records that the store cannot today separate never_tagged from "
             "tagged_zero from company_had_none: 159,522 empty-value rows were "
             "counted and discarded at ingest.")),
)


# -- 1c. SHAFFER-DERIVED: company level ------------------------------------

_register(
    _d("ebitda",
       "ebitda = operating_income + depreciation_amortisation",
       "What did the business earn before financing, tax and capital consumption?",
       [Input("operating_income"), Input("depreciation_amortisation")],
       unit="usd",
       note=("NOT an XBRL concept. It is assembled, both components must share "
             "period_end and qtrs, and pit_policy.ebitda_assembly_spec() versions "
             "the assembly. This is the one node with a MEASURED joint.")),
    _d("ebitda_margin",
       "ebitda_margin = ebitda / revenue",
       "How many cents of operating cash does each dollar of sales leave behind?",
       [Input("ebitda"), Input("revenue")]),
    _d("ebitda_growth",
       "ebitda_growth = (ebitda_t - ebitda_t-1) / abs(ebitda_t-1)",
       "Is the earnings power larger than it was a year ago?",
       [Input("ebitda"), Input("ebitda", lag=1)],
       note="Absolute denominator: a loss shrinking from -100 to -50 is an improvement."),
    _d("ebitda_acceleration",
       "ebitda_acceleration = ebitda_growth_t - ebitda_growth_t-1",
       "Is the earnings power growing FASTER than it was?",
       [Input("ebitda_growth"), Input("ebitda_growth", lag=1)],
       note=("Three EBITDA observations deep, so five primitive leaves at three "
             "dates. This is why acceleration is the scarcest growth component "
             "and why its scarcity is structural rather than a tagging problem.")),
    _d("revenue_growth",
       "revenue_growth = (revenue_t - revenue_t-1) / abs(revenue_t-1)",
       "Is the business selling more than it was a year ago?",
       [Input("revenue"), Input("revenue", lag=1)]),
    _d("revenue_acceleration",
       "revenue_acceleration = revenue_growth_t - revenue_growth_t-1",
       "Is the top line growing faster or slower than it was?",
       [Input("revenue_growth"), Input("revenue_growth", lag=1)]),
    _d("inflation",
       "inflation = cpi_t / cpi_t-1 - 1",
       "How much did the price level move over the same window?",
       [Input("cpi"), Input("cpi", lag=1)],
       scope=SCOPE_MACRO),
    _d("real_revenue_growth",
       "real_revenue_growth = (1 + revenue_growth) / (1 + inflation) - 1",
       "Did the business sell MORE, or just charge more?",
       [Input("revenue_growth"), Input("inflation")],
       note=("The deflated form, not the subtraction. Over a 7.1% nominal and a "
             "1.3% inflation the two differ by 6 basis points; over a 1980s "
             "double-digit pair they differ by whole points.")),
    _d("free_cash_flow",
       "free_cash_flow = operating_cash_flow - capex",
       "What cash is left after keeping the assets standing?",
       [Input("operating_cash_flow"), Input("capex")],
       unit="usd"),
    _d("market_cap",
       "market_cap = price_adjusted * shares_outstanding",
       "What does the market say the equity is worth?",
       [Input("price_adjusted"), Input("shares_outstanding")],
       unit="usd",
       note="The share count is the binding half of this product, not the price."),
    _d("enterprise_value",
       "enterprise_value = market_cap + total_debt - cash",
       "What would it cost to buy the whole business, debt included?",
       [Input("market_cap"), Input("total_debt"), Input("cash")],
       unit="usd"),
    _d("net_debt",
       "net_debt = total_debt - cash",
       "What is owed, net of what is already in hand?",
       [Input("total_debt"), Input("cash")],
       unit="usd"),
    _d("pe_ratio",
       "pe_ratio = market_cap / net_income",
       "How many years of current earnings is the market paying?",
       [Input("market_cap"), Input("net_income")]),
    _d("ev_ebitda",
       "ev_ebitda = enterprise_value / ebitda",
       "How many years of operating cash is the whole business priced at?",
       [Input("enterprise_value"), Input("ebitda")],
       note="Valid only inside [0.1, 300]; outside that band it is not a multiple."),
    _d("fcf_yield",
       "fcf_yield = free_cash_flow / market_cap",
       "What cash return does a share buy at today's price?",
       [Input("free_cash_flow"), Input("market_cap")]),
    _d("net_debt_ebitda",
       "net_debt_ebitda = net_debt / ebitda",
       "How many years of earnings would clear the debt?",
       [Input("net_debt"), Input("ebitda")]),
    _d("debt_market_cap",
       "debt_market_cap = total_debt / market_cap",
       "How much leverage sits under each dollar of equity value?",
       [Input("total_debt"), Input("market_cap")]),
    _d("interest_coverage",
       "interest_coverage = operating_income / interest_expense",
       "How many times over can the business pay its interest bill?",
       [Input("operating_income"), Input("interest_expense")]),
    _d("roa",
       "roa = net_income / total_assets",
       "How much profit does each dollar of assets generate?",
       [Input("net_income"), Input("total_assets")]),
    _d("roe",
       "roe = net_income / equity",
       "How much profit does each dollar of shareholder capital generate?",
       [Input("net_income"), Input("equity")]),
    _d("fcf_conversion",
       "fcf_conversion = free_cash_flow / ebitda",
       "How much of the accounting earnings turns into spendable cash?",
       [Input("free_cash_flow"), Input("ebitda")]),
)


# -- 1d. SHAFFER-DERIVED: COHORT LEVEL -------------------------------------
# These are the nodes the owner's correction is really about. Each is computed
# ONCE per (sector rung, key, as_of) and consumed by every member of that
# cohort. No provider publishes a single one of them, and that fact says
# nothing whatever about whether they are computable.

_register(
    _d("peer_set",
       "peer_set = members of the first SIC rung with >= MIN_INDUSTRY_PEERS members",
       "Who was this company actually competing with on that day?",
       [Input("sic", gate=True), Input("sic", per_member=True)],
       scope=SCOPE_COHORT, unit="count", member_basis="sector", min_members=5,
       note="39,038 peer sets exist over 165 as-of dates; mean size 31.8 members."),
    _d("sector_ebitda_vector",
       "sector_ebitda_vector = sorted(ebitda_i for i in peer_set"
       " if ev_ebitda_i is a usable multiple in [0.1, 300])",
       "What does the distribution of earnings power look like in this sector?",
       [Input("peer_set", gate=True), Input("ev_ebitda", per_member=True)],
       scope=SCOPE_COHORT, unit="count", member_basis="sector",
       min_members=MIN_EBITDA_COHORT,
       note=("FAITHFUL to the production core and worth arguing about: "
             "build_ebitda_peer_cohort filters to peers with a USABLE EV/EBITDA "
             "FIRST and takes the percentiles of that subset, so these are not "
             "the sector's EBITDA percentiles -- they are the PRICED sector's "
             "EBITDA percentiles. Everything downstream of the benchmark rests "
             "on it, and it is the node that makes the valuation factor depend "
             "on shares, price, debt and cash for ~32 other companies. Its "
             "VALUE in a trace and in pit_cohort_stat is the member COUNT; the "
             "vector itself lives only in the replay's memory.")),
    _d("sector_ebitda_p50",
       "sector_ebitda_p50 = percentile(sector_ebitda_vector, 0.50)",
       "What is a median-sized earner in this sector?",
       [Input("sector_ebitda_vector")],
       scope=SCOPE_COHORT, unit="usd", member_basis="sector"),
    _d("sector_ebitda_p75",
       "sector_ebitda_p75 = percentile(sector_ebitda_vector, 0.75)",
       "Where does the upper quartile of earning power begin?",
       [Input("sector_ebitda_vector")],
       scope=SCOPE_COHORT, unit="usd", member_basis="sector"),
    _d("cohort_50_75_membership",
       "cohort_50_75_membership = [i in sector_ebitda_vector : p50 <= ebitda_i <= p75]",
       "Which peers are the right size to price this company against?",
       [Input("sector_ebitda_p50", gate=True), Input("sector_ebitda_p75", gate=True),
        Input("ev_ebitda", per_member=True)],
       scope=SCOPE_COHORT, unit="count", member_basis="sector",
       min_members=MIN_EBITDA_COHORT * 4,
       note=("Deliberately the 50-75 band and not the top quartile: the very "
             "largest peers are priced for scale and would drag the benchmark. "
             "The threshold is 12 and not 3 for an arithmetic reason -- the band "
             "is a QUARTER of the vector by construction, so three members IN "
             "the band needs twelve members in the vector.")),
    _d("cohort_mean_ebitda",
       "cohort_mean_ebitda = mean(ebitda_i for i in cohort_50_75_membership)",
       "What does a typical right-sized peer earn?",
       [Input("cohort_50_75_membership")],
       scope=SCOPE_COHORT, unit="usd", member_basis="cohort",
       note=("THE node that started the correction. No API publishes it and no "
             "API ever could; it is a statistic over a cohort we defined. It "
             "takes no per-member input because membership already REQUIRES a "
             "resolved EBITDA -- charging for it twice would be double counting.")),
    _d("cohort_mean_margin",
       "cohort_mean_margin = mean(ebitda_i / revenue_i for i in cohort_50_75_membership)",
       "How profitable is a typical right-sized peer, per dollar of sales?",
       [Input("cohort_50_75_membership", gate=True), Input("revenue", per_member=True)],
       scope=SCOPE_COHORT, member_basis="cohort", min_members=MIN_EBITDA_COHORT,
       note=("Revenue is the ONLY extra primitive a margin needs over a member "
             "that is in the cohort already, so revenue is the only per-member "
             "charge here.")),
    _d("cohort_mean_ev_ebitda",
       "cohort_mean_ev_ebitda = winsorized_mean(ev_ebitda_i for i in cohort, 0.05, 0.95)",
       "What multiple is the market paying for a right-sized peer's earnings?",
       [Input("cohort_50_75_membership")],
       scope=SCOPE_COHORT, member_basis="cohort",
       note=("The valuation benchmark, and it costs nothing beyond membership: "
             "the vector was already filtered to peers with a usable multiple. "
             "The whole price of the benchmark is paid upstream, at "
             "sector_ebitda_vector, which is exactly why the valuation factor's "
             "binding constraint is COHORT FORMATION and not company data.")),
    _d("sector_pe",
       "sector_pe = mean(pe_ratio_i for i in peer_set)",
       "What is the sector paying for a dollar of earnings?",
       [Input("peer_set", gate=True), Input("pe_ratio", per_member=True)],
       scope=SCOPE_COHORT, member_basis="sector", min_members=MIN_EBITDA_COHORT),
    _d("sector_growth",
       "sector_growth = sector_return_acceleration - vti_return_acceleration",
       "Is this sector speeding up or slowing down against the whole market?",
       [Input("peer_set", gate=True), Input("price_adjusted", per_member=True)],
       scope=SCOPE_COHORT, unit="pct", member_basis="sector", min_members=MIN_EBITDA_COHORT),
    _d("sector_roe",
       "sector_roe = mean(roe_i for i in peer_set)",
       "How well does the sector convert shareholder capital into profit?",
       [Input("peer_set", gate=True), Input("roe", per_member=True)],
       scope=SCOPE_COHORT, member_basis="sector", min_members=MIN_EBITDA_COHORT),
    _d("sector_roa",
       "sector_roa = mean(roa_i for i in peer_set)",
       "How well does the sector convert assets into profit?",
       [Input("peer_set", gate=True), Input("roa", per_member=True)],
       scope=SCOPE_COHORT, member_basis="sector", min_members=MIN_EBITDA_COHORT),
    _d("sector_debt_market_cap",
       "sector_debt_market_cap = mean(debt_market_cap_i for i in peer_set)",
       "How levered is the sector as a whole?",
       [Input("peer_set", gate=True), Input("debt_market_cap", per_member=True)],
       scope=SCOPE_COHORT, member_basis="sector", min_members=MIN_EBITDA_COHORT),
)


# -- 1e. SHAFFER-DERIVED: company against its cohort -----------------------

_register(
    _d("ebitda_percentile",
       "ebitda_percentile = percentile_rank_within(ebitda, sector_ebitda_vector)",
       "How large an earner is this company, inside its own sector?",
       [Input("ebitda", gate=True), Input("sector_ebitda_vector", gate=True)]),
    _d("cohort_membership_flag",
       "cohort_membership_flag = (sector_ebitda_p50 <= ebitda <= sector_ebitda_p75)",
       "Is this company itself one of the right-sized peers?",
       [Input("ebitda"), Input("sector_ebitda_p50"), Input("sector_ebitda_p75")],
       unit="count"),
    _d("ebitda_excess_level",
       "ebitda_excess_level = ebitda - cohort_mean_ebitda",
       "In dollars, how far above or below the right-sized peer does it earn?",
       [Input("ebitda"), Input("cohort_mean_ebitda")],
       unit="usd"),
    _d("ebitda_benchmark_gap",
       "ebitda_benchmark_gap = ebitda / cohort_mean_ebitda - 1",
       "In proportion, how far above or below the benchmark does it earn?",
       [Input("ebitda"), Input("cohort_mean_ebitda")]),
    _d("ebitda_scale",
       "ebitda_scale = ebitda / sector_ebitda_p50",
       "How many median peers would it take to make this company?",
       [Input("ebitda"), Input("sector_ebitda_p50")],
       note="Size relative to the sector median, not an absolute-dollar size."),
    _d("ebitda_margin_rank",
       "ebitda_margin_rank = percentile_rank_within(ebitda_margin, peer margins)",
       "Is this company more profitable per dollar of sales than its peers?",
       [Input("ebitda_margin", gate=True), Input("peer_set", gate=True),
        Input("ebitda_margin", per_member=True)],
       member_basis="sector", min_members=MIN_EBITDA_COHORT),
    _d("margin_excess",
       "margin_excess = ebitda_margin - cohort_mean_margin",
       "How many points of margin above the right-sized peer?",
       [Input("ebitda_margin"), Input("cohort_mean_margin")]),
)


# -- 1f. SHAFFER-DERIVED: the valuation chain ------------------------------

_register(
    _d("implied_enterprise_value",
       "implied_enterprise_value = ebitda * cohort_mean_ev_ebitda",
       "What would the business be worth at the right-sized peer's multiple?",
       [Input("ebitda"), Input("cohort_mean_ev_ebitda")],
       unit="usd"),
    _d("implied_equity_value",
       "implied_equity_value = implied_enterprise_value - total_debt + cash",
       "And what would be left for shareholders after the lenders?",
       [Input("implied_enterprise_value"), Input("total_debt"), Input("cash")],
       unit="usd"),
    _d("implied_price",
       "implied_price = implied_equity_value / shares_outstanding",
       "What should one share be worth on that reasoning?",
       [Input("implied_equity_value"), Input("shares_outstanding")],
       unit="usd"),
    _d("valuation_gap",
       "valuation_gap = (implied_price - price_adjusted) / price_adjusted",
       "How far is the market price from the peer-implied price?",
       [Input("implied_price"), Input("price_adjusted")],
       note="Positive means the peers say it is cheap."),
    _d("valuation_score",
       "valuation_score = clamp(100 * tanh(2.0 * valuation_gap), -100, +100)",
       "How much should that mispricing move the score?",
       [Input("valuation_gap")],
       unit="score"),
    _d("extreme_valuation_penalty",
       "extreme_valuation_penalty = 100*tanh(2*gap) - 200*gap",
       "How much of an extreme apparent mispricing did we REFUSE to believe?",
       [Input("valuation_gap"), Input("valuation_score")],
       unit="score",
       note=("The production core has no separate additive penalty term, and "
             "inventing one here would misdescribe it. The penalty IS the tanh "
             "saturation, and this node measures it: the signed points the "
             "saturation removed against an unbounded linear response of 200*gap. "
             "It is ~0 for small gaps and grows without limit for large ones -- a "
             "gap of +400% scores +800 on the linear response and +99.99 here, "
             "so the node reports a refusal of 700 points. "
             "The second half of the same refusal is the [0.1, 300] validity band "
             "on ev_ebitda, which rejects a multiple rather than scoring it.")),
)


# -- 1g. SHAFFER-DERIVED: the scores ---------------------------------------
# Note the COMBINE_ANY. The production core renormalises over whatever
# resolved, so these nodes do NOT require all their inputs, and a coverage
# model that multiplied them would be describing a different product.

_register(
    _d("growth_score",
       "growth_score = 0.45*rank(revenue_growth) + 0.35*rank(revenue_acceleration)"
       " + 0.20*rank(ebitda_growth), weights renormalised over what resolved",
       "Is this company growing faster than its peers?",
       [Input("revenue_growth"), Input("revenue_acceleration"), Input("ebitda_growth"),
        Input("peer_set", gate=True)],
       unit="score", combine=COMBINE_ANY, min_inputs=1),
    _d("profitability_score",
       "profitability_score = 0.65*rank(ebitda_margin) + 0.35*rank(roa),"
       " weights renormalised over what resolved",
       "Is this company more profitable than its peers?",
       [Input("ebitda_margin"), Input("roa"), Input("peer_set", gate=True)],
       unit="score", combine=COMBINE_ANY, min_inputs=1),
    _d("debt_score",
       "debt_score = 0.60*rank(net_debt_ebitda) + 0.40*rank(debt_market_cap),"
       " both lower-is-better, weights renormalised over what resolved",
       "Is this company's balance sheet stronger than its peers'?",
       [Input("net_debt_ebitda"), Input("debt_market_cap"), Input("peer_set", gate=True)],
       unit="score", combine=COMBINE_ANY, min_inputs=1),
    _d("company_raw_score",
       "company_raw_score = 0.40V + 0.25G + 0.20P + 0.15D,"
       " weights renormalised over the factors that resolved",
       "All in, how good is this company against its peers?",
       [Input("valuation_score"), Input("growth_score"),
        Input("profitability_score"), Input("debt_score")],
       unit="score", combine=COMBINE_ANY, min_inputs=1,
       note=("Losing valuation costs 40% of the weight and is a DIFFERENT model "
             "state from losing debt; pit_score stores effective_weights_json so "
             "the two can never be pooled.")),
    _d("company_score",
       "company_score = clamp(0.75 * company_raw_score, -75, +75)",
       "The company verdict, on the scale the product reports.",
       [Input("company_raw_score")],
       unit="score"),
    _d("sector_score",
       "sector_score = 0.40*rank(sector_growth) + 0.21*rank(sector_roe)"
       " + 0.14*rank(sector_roa) + 0.25*rank(sector_debt_market_cap),"
       " weights renormalised over what resolved",
       "Is this a good sector to be in right now?",
       [Input("sector_growth"), Input("sector_roe"), Input("sector_roa"),
        Input("sector_debt_market_cap")],
       scope=SCOPE_COHORT, unit="score", combine=COMBINE_ANY, min_inputs=1,
       member_basis="sector"),
    _d("sector_overlay",
       "sector_overlay = clamp(sector_score / 4.0, -25, +25)",
       "How much should the sector environment move a company's score?",
       [Input("sector_score")],
       scope=SCOPE_COHORT, unit="score"),
    _d("shaffer_score",
       "shaffer_score = clamp(company_score + sector_overlay, -100, +100)",
       "The final verdict: this company, in this sector, on this date.",
       [Input("company_score"), Input("sector_overlay", required=False)],
       unit="score",
       note=("The overlay is OPTIONAL by design -- a missing sector environment "
             "contributes zero rather than blocking the score -- so it must not "
             "enter the joint availability. The replay says it was missing.")),
)


# ==========================================================================
# (2) GRAPH MECHANICS
# ==========================================================================

def node(key: str) -> Node:
    """One node. Raises on an unknown key rather than inventing an empty one."""
    try:
        return NODES[key]
    except KeyError:
        raise ValueError(
            f"unknown node {key!r}; {len(NODES)} are registered") from None


def node_keys() -> tuple[str, ...]:
    """Every node key, in a stable order."""
    return tuple(sorted(NODES))


def nodes_of_kind(kind: str) -> tuple[str, ...]:
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; known: {KINDS}")
    return tuple(sorted(k for k, n in NODES.items() if n.kind == kind))


def primitives() -> tuple[str, ...]:
    return nodes_of_kind(KIND_SOURCE)


def derived() -> tuple[str, ...]:
    return nodes_of_kind(KIND_DERIVED)


def cohort_nodes() -> tuple[str, ...]:
    """Nodes computed ONCE per (sector, as_of) and consumed by every member."""
    return tuple(sorted(k for k, n in NODES.items() if n.scope == SCOPE_COHORT))


def topological_order() -> tuple[str, ...]:
    """Every node, inputs strictly before consumers. Raises on a cycle.

    Kahn's algorithm, with ties broken by REGISTRATION ORDER so the order is
    stable across runs -- a trace that reorders itself between two runs is a
    trace nobody can diff. Registration order and not alphabetical because the
    registry is written in the order the model builds: revenue, operating
    income, D&A, ... and a trace that opens on `capex` because c precedes r
    reads like a dump rather than a derivation.
    """
    rank = {key: i for i, key in enumerate(NODES)}
    indegree = {k: len(set(i.key for i in n.inputs)) for k, n in NODES.items()}
    consumers: dict[str, set[str]] = {k: set() for k in NODES}
    for key, item in NODES.items():
        for dep in set(i.key for i in item.inputs):
            consumers.setdefault(dep, set()).add(key)

    ready = sorted((k for k, d in indegree.items() if d == 0), key=rank.get)
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for consumer in sorted(consumers.get(current, ()), key=rank.get):
            indegree[consumer] -= 1
            if indegree[consumer] == 0:
                ready.append(consumer)
        ready.sort(key=rank.get)
    if len(order) != len(NODES):
        stuck = sorted(set(NODES) - set(order))
        raise ValueError(f"the derivation graph has a cycle through {stuck}")
    return tuple(order)


def is_acyclic() -> bool:
    """Whether the graph is a DAG. A cycle would make every walk here a hang."""
    try:
        topological_order()
    except ValueError:
        return False
    return True


def ancestors(key: str) -> tuple[str, ...]:
    """Every node `key` depends on, transitively, in topological order."""
    node(key)
    seen: set[str] = set()
    stack = [key]
    while stack:
        current = stack.pop()
        for dep in set(i.key for i in node(current).inputs):
            if dep not in seen:
                seen.add(dep)
                stack.append(dep)
    order = topological_order()
    return tuple(k for k in order if k in seen)


def descendants(key: str) -> tuple[str, ...]:
    """Every node that depends on `key`, transitively, in topological order."""
    node(key)
    seen: set[str] = set()
    changed = True
    while changed:
        changed = False
        for candidate, item in NODES.items():
            if candidate in seen:
                continue
            deps = set(i.key for i in item.inputs)
            if key in deps or deps & seen:
                seen.add(candidate)
                changed = True
    order = topological_order()
    return tuple(k for k in order if k in seen)


def between(start: str, end: str) -> tuple[str, ...]:
    """Every node on some path from `start` to `end`, endpoints included.

    This is what makes "show me the walk from revenue to ShafferScore" a
    question with an exact answer rather than a rendering preference.
    """
    forward = set(descendants(start)) | {start}
    backward = set(ancestors(end)) | {end}
    both = forward & backward
    if start not in both or end not in both:
        return ()
    return tuple(k for k in topological_order() if k in both)


@dataclass(frozen=True)
class LeafRef:
    """One endpoint of a flattened derivation: a primitive, or a stopping node.

    Flattening stops at a COMBINE_ANY node and at a per-member (cohort) edge,
    because neither is a conjunction of primitives and pretending otherwise is
    exactly the arithmetic error this module is here to prevent.

    A per-member edge carries its OWNER and that owner's threshold, and the
    two are part of its identity. `ev_ebitda` for three peers (enough to take
    percentiles at all) and `ev_ebitda` for twelve (enough to leave three
    inside the 50-75 band) are DIFFERENT requirements on the same fact, and
    collapsing them would report the harder one at the easier one's rate.
    """

    key: str
    lag: int = 0
    per_member: bool = False
    stop_reason: str = "primitive"
    owner: str = ""
    min_members: int = 0

    @property
    def leaf_id(self) -> str:
        if self.per_member:
            return f"{self.key}[>= {self.min_members} peers]"
        if self.lag:
            return f"{self.key}@t-{self.lag}y"
        return self.key


def leaves(key: str, *, stop_at: Iterable[str] = (),
           base_lag: int = 0) -> tuple[LeafRef, ...]:
    """The primitive leaves `key` needs, with lags, de-duplicated and ordered.

    `stop_at` names nodes to treat as leaves even though they are derived --
    that is how a MEASURED joint is substituted for an estimated one (stop at
    `ebitda` and use the measured 51.3% instead of multiplying 78.0% by 62.8%).

    Optional inputs are excluded: ShafferScore does not need the sector overlay
    and must not be charged for it.

    DE-DUPLICATION IS THE POINT, not a tidiness. EBITDA acceleration is
    growth(t) minus growth(t-1), and those two share the t-1 observation.
    Multiplying the two growth terms charges for that observation TWICE and
    reports 6.25% where the honest independence figure is 12.5%. The leaf set
    is the unit the joint must be computed over, which is why `coverage`
    computes conjunctions from this function's output rather than by
    multiplying its way up the tree.

    `base_lag` shifts the whole subtree, so the leaves of `ebitda@t-1y` come
    back as `operating_income@t-1y` and `depreciation_amortisation@t-1y`.
    """
    stops = set(stop_at) - {key}
    out: list[LeafRef] = []
    seen: set[tuple[str, int, bool, str]] = set()

    def emit(ref: LeafRef) -> None:
        identity = (ref.key, ref.lag, ref.per_member, ref.owner)
        if identity not in seen:
            seen.add(identity)
            out.append(ref)

    def walk(current: str, lag: int, path: tuple[str, ...]) -> None:
        if current in path:
            raise ValueError(f"cycle in the derivation graph at {current!r}")
        item = node(current)
        root = not path
        if (item.is_primitive or item.kind == KIND_UNAVAILABLE
                or (not root and (current in stops or item.combine == COMBINE_ANY))):
            emit(LeafRef(current, lag, False,
                         "primitive" if item.is_primitive else
                         "genuinely_unavailable" if item.kind == KIND_UNAVAILABLE else
                         ("measured_joint" if lag == 0 else "estimated_subjoint")
                         if current in stops else "renormalising_node"))
            return
        for edge in item.required_inputs:
            if edge.per_member:
                emit(LeafRef(edge.key, lag + edge.lag, True, "per_member_cohort",
                             owner=current,
                             min_members=max(item.min_members, MIN_EBITDA_COHORT)))
            else:
                walk(edge.key, lag + edge.lag, path + (current,))

    root_node = node(key)
    if root_node.combine == COMBINE_ANY:
        # A renormalising node is not a conjunction of primitives, so flattening
        # it would answer a question nobody asked. Its DIRECT inputs are the
        # honest unit of report: this factor survives on any one of them.
        for edge in root_node.required_inputs:
            out.append(LeafRef(
                edge.key, base_lag + edge.lag, edge.per_member,
                "gate_input" if edge.gate else "blend_input",
                owner=key if edge.per_member else "",
                min_members=max(root_node.min_members, MIN_EBITDA_COHORT)))
        return tuple(out)

    walk(key, base_lag, ())
    return tuple(out)



def validate_graph() -> list[str]:
    """Every structural problem in the registry, as plain sentences.

    Run by the tests rather than at import: a module that refuses to import
    cannot tell you WHY, and this list is the diagnostic.
    """
    problems: list[str] = []
    for key, item in NODES.items():
        if item.key != key:
            problems.append(f"{key}: node.key is {item.key!r}")
        if item.kind not in KINDS:
            problems.append(f"{key}: unknown kind {item.kind!r}")
        if item.scope not in (SCOPE_COMPANY, SCOPE_COHORT, SCOPE_MACRO):
            problems.append(f"{key}: unknown scope {item.scope!r}")
        if item.kind == KIND_SOURCE and item.inputs:
            problems.append(f"{key}: a SOURCE_PRIMITIVE must have no inputs")
        if item.kind == KIND_UNAVAILABLE and item.inputs:
            problems.append(f"{key}: a GENUINELY_UNAVAILABLE primitive has inputs")
        if item.kind == KIND_DERIVED and not item.inputs:
            problems.append(f"{key}: a SHAFFER_DERIVED node with no inputs has no provenance")
        if item.kind == KIND_SOURCE and not item.source:
            problems.append(f"{key}: a primitive must name the ladder or table that supplies it")
        if item.kind == KIND_DERIVED and "=" not in item.formula:
            problems.append(f"{key}: the formula must state the arithmetic")
        if not item.question.endswith("?") and item.kind == KIND_DERIVED:
            if not item.question.endswith("."):
                problems.append(f"{key}: the economic question is not a question")
        for edge in item.inputs:
            if edge.key not in NODES:
                problems.append(f"{key}: input {edge.key!r} is not a registered node")
            if edge.lag < 0:
                problems.append(f"{key}: input {edge.key!r} has a negative lag")
        if item.combine == COMBINE_ANY and item.kind != KIND_DERIVED:
            problems.append(f"{key}: only a derived node may renormalise")
        if item.combine not in (COMBINE_ALL, COMBINE_ANY):
            problems.append(f"{key}: unknown combine rule {item.combine!r}")
    if not is_acyclic():
        problems.append("the graph is not acyclic")
    return problems


# ==========================================================================
# (3) MEASURED COVERAGE
# ==========================================================================

#: The three census dates. Everything below is measured AT these dates against
#: a base of peers with a usable, non-stale Assets fact.
MEASURED_DATES = ("2015-06-30", "2019-06-28", "2024-06-28")

#: Base universe size at each date: peers with a usable non-stale Assets fact.
MEASURED_BASE = {"2015-06-30": 7073, "2019-06-28": 5959, "2024-06-28": 5944}

#: Entities in the scored universe with a resolvable listing (hence a price).
#: pit_listing holds 2,574 listings in total.
MEASURED_PRICED_ENTITIES = {"2015-06-30": 2065, "2019-06-28": 2455, "2024-06-28": 2475}

#: Marginal availability of each SOURCE_PRIMITIVE, as a fraction of base.
#: MEASURED on the loaded store; nothing here is modelled or interpolated.
#: A primitive absent from this table has NOT been surveyed and is reported as
#: unmeasured rather than assigned a plausible number.
MEASURED_COVERAGE: dict[str, dict[str, float]] = {
    "revenue":                   {"2015-06-30": 0.706, "2019-06-28": 0.778, "2024-06-28": 0.772},
    "net_income":                {"2015-06-30": 0.951, "2019-06-28": 0.966, "2024-06-28": 0.978},
    "operating_income":          {"2015-06-30": 0.701, "2019-06-28": 0.741, "2024-06-28": 0.780},
    "depreciation_amortisation": {"2015-06-30": 0.614, "2019-06-28": 0.638, "2024-06-28": 0.628},
    "cash":                      {"2015-06-30": 0.907, "2019-06-28": 0.934, "2024-06-28": 0.954},
    "total_assets":              {"2015-06-30": 1.000, "2019-06-28": 1.000, "2024-06-28": 1.000},
    "equity":                    {"2015-06-30": 0.947, "2019-06-28": 0.963, "2024-06-28": 0.975},
    "operating_cash_flow":       {"2015-06-30": 0.942, "2019-06-28": 0.961, "2024-06-28": 0.973},
    "capex":                     {"2015-06-30": 0.675, "2019-06-28": 0.711, "2024-06-28": 0.694},
    "interest_expense":          {"2015-06-30": 0.638, "2019-06-28": 0.607, "2024-06-28": 0.558},
    "shares_outstanding":        {"2015-06-30": 0.2535, "2019-06-28": 0.3520, "2024-06-28": 0.4135},
    "cpi":                       {"2015-06-30": 1.000, "2019-06-28": 1.000, "2024-06-28": 1.000},
    "sic":                       {"2015-06-30": 1.000, "2019-06-28": 1.000, "2024-06-28": 1.000},
}

#: total_debt depends on WHICH ladder version resolved it, and the difference
#: is 2.4x. Keeping the two side by side is the point: a coverage report that
#: did not say which ladder produced it would be unreadable a year from now.
MEASURED_TOTAL_DEBT: dict[str, dict[str, float]] = {
    "concept_ladder_v1": {"2015-06-30": 0.159, "2019-06-28": 0.178, "2024-06-28": 0.145},
    "concept_ladder_v2": {"2015-06-30": 0.379, "2019-06-28": 0.423, "2024-06-28": 0.377},
}

#: The one MEASURED JOINT in the model: EBITDA resolves when operating income
#: AND D&A both resolve at a matched period. This is the calibration point for
#: every estimated joint in the module.
MEASURED_JOINTS: dict[str, dict[str, float]] = {
    "ebitda": {"2015-06-30": 0.481, "2019-06-28": 0.507, "2024-06-28": 0.513},
}

#: Why `shares_outstanding` carries the small number and not the large one.
SHARES_NOTE = (
    "shares_outstanding is 61.1/64.9/63.5% of base at the raw ladder and "
    "25.35/35.20/41.35% after the share-class guard. The DEFENSIBLE figure is "
    "used throughout: a per-class CommonStockSharesOutstanding taken as the "
    "whole-company count mis-scales market cap, enterprise value, P/E, "
    "EV/EBITDA and debt/market cap all at once, in the same direction, for "
    "that entity. A larger coverage number bought that way is not coverage.")

#: Why price is DERIVED FROM COUNTS rather than quoted from a survey.
PRICE_NOTE = (
    "price availability is not a surveyed percentage: it is "
    "scored_universe / base (2,065/7,073, 2,455/5,959, 2,475/5,944) from "
    "pit_listing's 2,574 listings. It is a share OF THE FACT BASE, which is "
    "the denominator every other number here uses, and it is the reason the "
    "valuation factor is thinner than the fundamentals suggest.")


def primitive_availability(as_of: str,
                           ladder_version: str = "concept_ladder_v2") -> dict[str, float]:
    """Measured marginal availability of every surveyed primitive at `as_of`.

    Returns a plain {primitive_key: fraction} mapping -- exactly the shape
    `coverage` and `coverage_report` want. Primitives with no survey
    (`treasury_yield`, `price_raw`) are simply ABSENT from the mapping rather
    than present with a guess; a consumer that needs one gets an explicit
    "unmeasured" in the report.

    `price_adjusted` is derived from the entity counts, see PRICE_NOTE;
    `total_debt` is selected by ladder version, see MEASURED_TOTAL_DEBT.
    """
    day = str(as_of)[:10]
    if day not in MEASURED_BASE:
        raise ValueError(
            f"no census at {day!r}; measured dates are {', '.join(MEASURED_DATES)}")
    out = {key: values[day] for key, values in MEASURED_COVERAGE.items()}
    try:
        out["total_debt"] = MEASURED_TOTAL_DEBT[ladder_version][day]
    except KeyError:
        raise ValueError(
            f"unknown ladder version {ladder_version!r}; known: "
            f"{', '.join(sorted(MEASURED_TOTAL_DEBT))}") from None
    out["price_adjusted"] = MEASURED_PRICED_ENTITIES[day] / MEASURED_BASE[day]
    return out


def co_occurrence_lift(as_of: str) -> float:
    """MEASURED joint / independence estimate for EBITDA at `as_of`.

    This is the number that says how wrong independence is. Company facts
    co-occur: a filer that tags operating income usually tags D&A in the same
    cash-flow statement, so the product understates. Measured:

        2015-06-30   0.701 x 0.614 = 0.4304   vs   0.481   -> lift 1.118
        2019-06-28   0.741 x 0.638 = 0.4728   vs   0.507   -> lift 1.072
        2024-06-28   0.780 x 0.628 = 0.4898   vs   0.513   -> lift 1.047

    The lift is FALLING as tagging practice matures, which is itself worth
    knowing: the older the as-of date, the more independence lies.
    """
    day = str(as_of)[:10]
    marginals = primitive_availability(day)
    product = (marginals["operating_income"]
               * marginals["depreciation_amortisation"])
    return MEASURED_JOINTS["ebitda"][day] / product


# ==========================================================================
# (4) COVERAGE PROPAGATION
# ==========================================================================

@dataclass(frozen=True)
class Estimate:
    """What we can honestly say about one node's joint availability.

    Four numbers, none of which is 'the answer', because there is no single
    honest answer without a measurement:

      independent  the product (conjunction) or 1-prod(1-p) (renormalising
                   node). A LOWER bound in practice, never an estimate.
      calibrated   the product scaled by the measured co-occurrence lift. The
                   weakest of the three: it extrapolates ONE measured pair to
                   n-way joints and is clamped to the Frechet ceiling.
      lower/upper  Frechet bounds. Assumption-free, often uselessly wide, and
                   the upper bound is always the scarcest marginal -- which is
                   to say it is the binding leaf, again.
      measured     the real thing, where one exists. Today: EBITDA only.
    """

    independent: float
    calibrated: float
    lower: float
    upper: float
    measured: Optional[float] = None

    @property
    def point(self) -> float:
        """The number to rank on: measured if we have it, else independent."""
        return self.measured if self.measured is not None else self.independent


def frechet_bounds(marginals: Sequence[float]) -> tuple[float, float]:
    """Assumption-free bounds on P(all of them), given only the marginals.

        max(0, sum - (n-1))  <=  joint  <=  min(marginals)

    The upper bound is exactly the scarcest marginal: no conjunction can be
    more available than its rarest member. That is why the binding leaf and
    the Frechet ceiling are always the same quantity.
    """
    values = [float(v) for v in marginals]
    if not values:
        return 1.0, 1.0
    lower = max(0.0, sum(values) - (len(values) - 1))
    return lower, min(values)


def independence_joint(marginals: Sequence[float]) -> float:
    """The product. A convenient fiction -- see the module docstring."""
    out = 1.0
    for value in marginals:
        out *= float(value)
    return out


def union_joint(marginals: Sequence[float]) -> tuple[float, float, float]:
    """(independent, frechet_lower, frechet_upper) for P(at least one).

    The renormalising nodes need this. A blend that drops missing factors and
    renormalises the rest survives on ANY one input, so its availability is a
    union, and the bounds flip: max(marginals) <= union <= min(1, sum).
    """
    values = [float(v) for v in marginals]
    if not values:
        return 0.0, 0.0, 0.0
    survive = 1.0
    for value in values:
        survive *= (1.0 - value)
    return 1.0 - survive, max(values), min(1.0, sum(values))


def binomial_at_least(n: int, p: float, k: int) -> float:
    """P(at least k successes in n independent Bernoulli(p) trials).

    Used for the cohort gates, and the independence assumption is WEAKER here
    than elsewhere: peers inside one SIC rung share an industry, an auditor
    population and a filing-software population, so their tagging outcomes are
    positively correlated and this understates formation probability. It is
    the same direction of error as everywhere else in this module.
    """
    n = int(n)
    k = int(k)
    if n <= 0:
        return 0.0
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    p = min(max(float(p), 0.0), 1.0)
    tail = 0.0
    for i in range(0, k):
        tail += math.comb(n, i) * (p ** i) * ((1.0 - p) ** (n - i))
    return max(0.0, min(1.0, 1.0 - tail))


def cohort_formation_probability(p_member: float,
                                 n_peers: float = PEER_SET_MEAN_SIZE,
                                 min_members: int = MIN_EBITDA_COHORT) -> float:
    """P(enough peers resolve for a cohort statistic to exist at all).

    The MEAN peer set has 31.8 members, and at the mean almost nothing can go
    wrong: 32 peers at 51.3% EBITDA coverage form a 3-member cohort with
    probability > 0.99999. That number is also close to meaningless, because
    the mean is not the distribution. At a 5-member SIC4 rung the same 51.3%
    gives 52.4%, and at the EV/EBITDA joint of ~3.8% a 32-member peer set
    forms a benchmark cohort only ~12% of the time.

    So this function is reported WITH its n, always. A cohort probability
    quoted without the peer count it assumes is not a result.
    """
    return binomial_at_least(int(round(n_peers)), p_member, min_members)


def _expected_members(owner: Node, band_driver: float,
                      peer_set_size: float = PEER_SET_MEAN_SIZE) -> float:
    """Expected members of the set `owner` is a statistic over.

    'sector' basis: the whole peer set, 31.8 members at the measured mean.
    'cohort' basis: the 50-75 band, which is a QUARTER of the vector BY
    CONSTRUCTION -- it is defined by percentiles, so its size is arithmetic,
    not an estimate. `band_driver` is the availability of the quantity that
    puts a peer into the vector in the first place.
    """
    if owner.member_basis == "cohort":
        return max(1.0, COHORT_BAND_FRACTION * peer_set_size * band_driver)
    return peer_set_size


def _owner_of(ref: "LeafRef") -> Optional[Node]:
    """The node that consumes `ref` as a per-member edge.

    A per-member edge's population and threshold belong to the CONSUMER, so a
    leaf row cannot be priced without knowing who asked for it. Returns None
    for an ordinary leaf, which needs no owner.
    """
    return node(ref.owner) if (ref.per_member and ref.owner) else None


def coverage(node_key: str,
             primitive_availability: Mapping[str, float],
             *,
             measured_joints: Optional[Mapping[str, float]] = None,
             lift: Optional[float] = None,
             use_measured_subjoints: bool = True,
             peer_set_size: float = PEER_SET_MEAN_SIZE) -> dict[str, Any]:
    """What actually prevents `node_key` from being computed, down to the leaf.

    This is the replacement for "feature X unavailable". It walks the DAG and
    returns, for the node:

      * the availability of each required primitive, individually;
      * the JOINT availability under three models (independence, calibrated,
        Frechet bracket), plus the measured joint where one exists;
      * the BINDING LEAF -- the primitive whose absence explains the most
        failures -- and what the joint would become if that one leaf were
        repaired to 100%.

    `primitive_availability` maps primitive key -> fraction; a lagged leaf may
    be supplied explicitly as "revenue@t-1y" and otherwise inherits the
    unlagged marginal (flagged `assumed: lag_persistence` in the result,
    because it IS an assumption: a company that filed last year and not this
    one is a different population from one that filed both).

    `measured_joints` overrides estimation for any node it names -- EBITDA by
    default, because its joint is measured and the estimate is 5 points wrong.
    Pass `use_measured_subjoints=False` to see the uncalibrated walk.

    Raises on a node whose required primitives are not all priced: a coverage
    number computed over a guessed marginal is worse than no number.
    """
    item = node(node_key)
    joints = dict(measured_joints or {})
    if not use_measured_subjoints:
        joints = {}
    #: The measured joint of the node being reported is NOT substituted for the
    #: node itself -- that is the whole point of asking for its coverage. It is
    #: attached beside the estimates so the two can be compared.
    own_measured = joints.pop(node_key, None)
    if lift is None:
        lift = 1.0

    notes: list[str] = []
    memo: dict[tuple[str, int, bool, str], Estimate] = {}
    gate_detail: dict[str, str] = {}

    def leaf_marginal(key: str, lag: int) -> tuple[float, str]:
        exact = f"{key}@t-{lag}y" if lag else key
        if exact in primitive_availability:
            return float(primitive_availability[exact]), "measured"
        if lag and key in primitive_availability:
            return float(primitive_availability[key]), "lag_persistence"
        raise ValueError(
            f"{node_key}: no availability supplied for primitive {exact!r}. "
            f"It has not been surveyed; supply a measured figure or accept "
            f"that this node's coverage is unknown -- do not guess one.")

    def estimate(key: str, lag: int = 0, per_member: bool = False,
                 owner: Optional[Node] = None) -> Estimate:
        identity = (key, lag, per_member, owner.key if owner else "")
        if identity in memo:
            return memo[identity]
        current = node(key)

        if per_member:
            # One value per cohort member: a COUNT THRESHOLD, not a conjunction.
            # The consuming node decides the population and the threshold,
            # which is why `owner` and not `current` supplies them.
            holder = owner or current
            inner = estimate(key, lag)
            band = _band_estimate()
            needed = max(holder.min_members, MIN_EBITDA_COHORT)

            def gate(p: float, driver: float) -> float:
                return cohort_formation_probability(
                    p, _expected_members(holder, driver, peer_set_size), needed)

            result = Estimate(
                gate(inner.point, band.point),
                gate(inner.calibrated, band.calibrated),
                gate(inner.lower, band.lower),
                gate(inner.upper, band.upper),
            )
            expected = _expected_members(holder, band.point, peer_set_size)
            gate_detail[(holder.key, key)] = (
                f">= {needed} of ~{expected:.1f} expected members, "
                f"each {inner.point:.1%}")
            notes.append(
                f"{holder.key}: needs {key} for >= {needed} members of an "
                f"expected {expected:.1f}, each available {inner.point:.1%} "
                f"-> {result.independent:.2%} .. {result.upper:.2%} that the "
                f"statistic exists at all. INDEPENDENCE IS WEAKEST HERE: peers "
                f"in one SIC rung share a filer population, so their tagging "
                f"outcomes are positively correlated and the low end is a floor.")
            memo[identity] = result
            return result

        if current.kind == KIND_UNAVAILABLE:
            result = Estimate(0.0, 0.0, 0.0, 0.0)
            memo[identity] = result
            return result

        if current.is_primitive:
            value, basis = leaf_marginal(key, lag)
            if basis == "lag_persistence":
                notes.append(
                    f"{key}@t-{lag}y: no separate survey; assumed equal to the "
                    f"t marginal of {value:.1%} (an ASSUMPTION, not a measurement)")
            result = Estimate(value, value, value, value)
            memo[identity] = result
            return result

        if key in joints and lag == 0:
            value = float(joints[key])
            result = Estimate(value, value, value, value, measured=value)
            memo[identity] = result
            return result

        if current.combine == COMBINE_ALL:
            # A conjunction is computed over its DE-DUPLICATED leaf set, never
            # by multiplying its way up the tree: EBITDA acceleration shares
            # the t-1 observation between its two growth terms, and a recursive
            # product charges for it twice (6.25% against an honest 12.5%).
            refs = leaves(key, stop_at=set(joints), base_lag=lag)
            children = [estimate(ref.key, ref.lag, ref.per_member,
                                 _owner_of(ref))
                        for ref in refs]
            points = [c.point for c in children]
            independent = independence_joint(points)
            n = max(1, len(children))
            low = max(0.0, sum(c.lower for c in children) - (n - 1))
            high = min((c.upper for c in children), default=1.0)
            # The lift is a CO-OCCURRENCE correction between uncertain facts.
            # Applying it across a child that is certain (CPI, total assets)
            # would manufacture correlation with a constant, so the exponent
            # counts only the children that can actually be absent.
            uncertain = sum(1 for c in children if c.point < 1.0)
            calibrated = min(high, independence_joint(
                [c.calibrated for c in children]) * (lift ** max(0, uncertain - 1)))
            result = Estimate(independent, calibrated, low, high)
            memo[identity] = result
            return result

        # A renormalising node survives on ANY ONE of its blended inputs, but
        # on ALL of its gates. Blended children may share leaves; a union is
        # far less sensitive to that than a product, and the bounds below
        # bracket it either way.
        gates = [estimate(edge.key, lag + edge.lag, edge.per_member, current)
                 for edge in current.gate_inputs]
        blend = [estimate(edge.key, lag + edge.lag, edge.per_member, current)
                 for edge in current.blend_inputs]
        union, _, _ = union_joint([c.point for c in blend])
        _, union_low, _ = union_joint([c.lower for c in blend])
        _, _, union_high = union_joint([c.upper for c in blend])
        gate_point = independence_joint([c.point for c in gates])
        gate_low = (max(0.0, sum(c.lower for c in gates) - (len(gates) - 1))
                    if gates else 1.0)
        gate_high = min((c.upper for c in gates), default=1.0)
        independent = union * gate_point
        result = Estimate(independent, independent,
                          max(0.0, union_low + gate_low - 1.0),
                          min(union_high, gate_high))
        memo[identity] = result
        return result

    _band_cache: list[Estimate] = []

    def _band_estimate() -> Estimate:
        """Availability of the quantity that decides who is in the vector.

        `build_ebitda_peer_cohort` filters to peers with a USABLE EV/EBITDA
        BEFORE taking percentiles, so that -- not bare EBITDA -- is what sizes
        the 50-75 band. Cached because it is asked for at every cohort edge and
        computing it re-enters the walk.
        """
        if not _band_cache:
            _band_cache.append(Estimate(1.0, 1.0, 1.0, 1.0))   # break re-entry
            try:
                _band_cache[0] = estimate("ev_ebitda")
            except ValueError:
                pass
        return _band_cache[0]

    result = estimate(node_key)
    if own_measured is not None:
        result = Estimate(result.independent, result.calibrated,
                          result.lower, result.upper, measured=float(own_measured))
    refs = leaves(node_key, stop_at=set(joints))

    rows: list[dict[str, Any]] = []
    for ref in refs:
        owner = _owner_of(ref)
        child = estimate(ref.key, ref.lag, ref.per_member, owner)
        rows.append({
            "leaf": ref.leaf_id,
            "key": ref.key,
            "availability": child.point,
            "kind": node(ref.key).kind,
            "stop_reason": ref.stop_reason,
            "measured": child.measured is not None,
            "detail": gate_detail.get((ref.owner, ref.key), ""),
        })

    conjunctive = item.combine == COMBINE_ALL
    ranked = sorted(rows, key=lambda r: r["availability"])
    binding = ranked[0] if (ranked and conjunctive) else None
    runner_up = ranked[1] if (len(ranked) > 1 and conjunctive) else None

    repaired: Optional[float] = None
    if binding is not None and len(rows) > 1:
        others = [r["availability"] for r in rows if r is not binding]
        repaired = independence_joint(others)

    if not conjunctive:
        notes.append(
            f"{node_key} RENORMALISES over what resolved: it needs "
            f"{max(1, item.min_inputs)} of {len(item.required_inputs)} inputs, so "
            f"no single leaf binds and its availability is a UNION, not a product.")

    return {
        "node": node_key,
        "kind": item.kind,
        "scope": item.scope,
        "formula": item.formula,
        "question": item.question,
        "combine": item.combine,
        "leaves": rows,
        "n_leaves": len(rows),
        "joint_independent": result.independent,
        "joint_calibrated": result.calibrated,
        "frechet_lower": result.lower,
        "frechet_upper": result.upper,
        "measured_joint": result.measured,
        "binding_leaf": binding["leaf"] if binding else None,
        "binding_availability": binding["availability"] if binding else None,
        "runner_up_leaf": runner_up["leaf"] if runner_up else None,
        "runner_up_availability": runner_up["availability"] if runner_up else None,
        "joint_if_binding_repaired": repaired,
        "lift": lift,
        "notes": notes,
    }


#: The ONE measurement that would settle the largest open question in this
#: report. Everything in the cohort half rests on a modelled cohort-formation
#: rate, and the model is a binomial over an INDEPENDENCE assumption that is
#: weaker here than anywhere else in the module: the peers that carry a price,
#: a share count, a debt figure and a cash figure are largely the SAME peers
#: (large, well-covered filers), and that correlation is exactly what a
#: binomial cannot see. So the numbers below are a floor, and the distance
#: between the floor and the Frechet ceiling is the entire uncertainty in the
#: 40%-weighted valuation factor.
NEXT_MEASUREMENT = (
    "COUNT, per (as_of_date, rung, key) over the 39,038 peer sets, how many "
    "members have ALL FIVE of price, shares, total_debt, cash and EBITDA at a "
    "matched period -- and report the DISTRIBUTION of that count, not its "
    "mean. One pass, one GROUP BY, no modelling. It replaces every cohort "
    "figure in this report with a measurement, and it is the difference "
    "between 'the valuation factor may be nearly unusable' and knowing.")

#: The derived features the coverage report covers by default: everything the
#: owner enumerated, in the order the computation builds them.
REPORT_NODES: tuple[str, ...] = (
    "ebitda", "ebitda_margin", "ebitda_growth", "ebitda_acceleration",
    "revenue_growth", "real_revenue_growth", "free_cash_flow",
    "market_cap", "enterprise_value", "pe_ratio", "ev_ebitda", "fcf_yield",
    "net_debt_ebitda", "interest_coverage", "roa", "fcf_conversion",
    "sector_ebitda_p50", "sector_ebitda_p75", "cohort_50_75_membership",
    "cohort_mean_ebitda", "cohort_mean_margin", "cohort_mean_ev_ebitda",
    "ebitda_benchmark_gap", "ebitda_excess_level", "ebitda_margin_rank",
    "ebitda_scale", "company_score", "sector_score", "shaffer_score",
)


def _wrap(text: str, width: int) -> list[str]:
    """Greedy line wrap. Stdlib textwrap would do, but this module imports one
    module and one module only, and a five-line wrapper is cheaper than the
    dependency audit."""
    out: list[str] = []
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def _pct(value: Optional[float]) -> str:
    return "     n/a" if value is None else f"{100.0 * value:7.2f}%"


def coverage_report(primitive_availability_map: Mapping[str, float],
                    *,
                    node_keys_wanted: Sequence[str] = REPORT_NODES,
                    label: str = "",
                    measured_joints: Optional[Mapping[str, float]] = None,
                    lift: Optional[float] = None,
                    width: int = 78) -> str:
    """The report that replaces every 'feature X unavailable' in this project.

    For every derived feature: each required primitive's availability, the
    joint under all the models we can honestly offer, and the BINDING LEAF.
    Plain text, because the point is that a person reads it and argues.
    """
    out: list[str] = []
    summary: list[tuple[str, dict[str, Any]]] = []
    rule = "=" * width
    out.append(rule)
    out.append(f"DERIVATION COVERAGE{(' -- ' + label) if label else ''}")
    out.append(rule)
    out.append("Every line below is a DERIVED quantity: ours to compute, never a")
    out.append("provider's to publish. A number is missing only when a NAMED")
    out.append("primitive underneath it is missing, and the binding leaf names it.")
    out.append("")
    out.append("How to read each block:")
    out.append("  'all primitives jointly' is the INDEPENDENCE estimate -- the product.")
    out.append("     Independence is a convenient fiction here. Company facts CO-OCCUR:")
    out.append("     a filer that tags one balance-sheet item usually tags the next one,")
    out.append("     so the product is a LOWER BOUND in practice, not an estimate.")
    out.append("  'calibrated' lifts that product by the co-occurrence measured on the")
    out.append("     one joint we have (EBITDA). It is the WEAKEST of the three numbers:")
    out.append("     it extrapolates one measured pair to n-way joints.")
    out.append("  'Frechet' is assumption-free: max(0, sum-(n-1)) <= joint <= min(...).")
    out.append("     Often uselessly wide, and its ceiling IS the binding leaf.")
    out.append("  'MEASURED' appears only where a real joint exists. Today: EBITDA.")
    out.append("  A leaf marked [>= k peers] is a COHORT GATE -- a count threshold over")
    out.append("     peers, not a conjunction, and the place independence is weakest.")
    out.append("")
    out.append("primitive marginals used:")
    for key in sorted(primitive_availability_map):
        out.append(f"    {key:<28}{_pct(primitive_availability_map[key])}")
    unmeasured = [k for k in primitives() if k not in primitive_availability_map]
    if unmeasured:
        out.append(f"    NOT SURVEYED (no figure quoted): {', '.join(sorted(unmeasured))}")
    if lift is not None:
        out.append(f"    co-occurrence lift (measured on EBITDA): {lift:.4f}")
    out.append("")

    for key in node_keys_wanted:
        report = coverage(key, primitive_availability_map,
                          measured_joints=measured_joints, lift=lift)
        item = node(key)
        out.append(f"{key} -- {item.question}")
        out.append(f"  {item.formula}")
        if item.scope == SCOPE_COHORT:
            out.append("  [COHORT-LEVEL: one value per (sector, as_of), shared by every member]")
        for row in report["leaves"]:
            flag = ""
            if row["measured"]:
                flag = "   (MEASURED joint)"
            elif row["stop_reason"] == "per_member_cohort":
                flag = f"   (cohort gate: {row['detail']})"
            elif row["stop_reason"] == "renormalising_node":
                flag = "   (renormalising sub-score)"
            elif row["stop_reason"] == "gate_input":
                flag = "   (gate: required even here)"
            out.append(f"    {row['leaf']:<28}{_pct(row['availability'])}{flag}")
        if report["combine"] == COMBINE_ANY:
            out.append(f"    any one input suffices:  {_pct(report['joint_independent'])}"
                       f"   [{_pct(report['frechet_lower']).strip()} .."
                       f" {_pct(report['frechet_upper']).strip()}]")
            out.append("    BINDING LEAF: none -- this node renormalises over "
                       "whatever resolved")
        else:
            out.append(f"    all primitives jointly:  {_pct(report['joint_independent'])}")
            measured = ("" if report["measured_joint"] is None else
                        f" | MEASURED {_pct(report['measured_joint']).strip()}")
            out.append(f"      calibrated {_pct(report['joint_calibrated']).strip()}"
                       f" | Frechet {_pct(report['frechet_lower']).strip()}"
                       f" .. {_pct(report['frechet_upper']).strip()}{measured}")
            if report["binding_leaf"]:
                line = (f"    BINDING LEAF: {report['binding_leaf']} "
                        f"({_pct(report['binding_availability']).strip()})")
                if report["runner_up_leaf"]:
                    line += (f"; next {report['runner_up_leaf']} "
                             f"({_pct(report['runner_up_availability']).strip()})")
                out.append(line)
                if report["joint_if_binding_repaired"] is not None:
                    out.append(f"      repair it to 100% and the joint becomes "
                               f"{_pct(report['joint_if_binding_repaired']).strip()}")
        summary.append((key, report))
        out.append("")

    out.append("-" * width)
    out.append("BINDING LEAF SUMMARY -- what to go and fix, in one table")
    out.append("-" * width)
    out.append(f"  {'feature':<26}{'joint':>9}{'ceiling':>10}  binding leaf")
    for key, report in summary:
        binding = report["binding_leaf"] or "(renormalises: none)"
        out.append(f"  {key:<26}" + _pct(report["joint_independent"])
                   + _pct(report["frechet_upper"]) + "  " + binding)
    tally: dict[str, int] = {}
    for _, report in summary:
        if report["binding_leaf"]:
            tally[report["binding_leaf"]] = tally.get(report["binding_leaf"], 0) + 1
    out.append("")
    out.append("  binding-leaf frequency across these features:")
    for leaf, count in sorted(tally.items(), key=lambda kv: (-kv[1], kv[0])):
        out.append(f"    {leaf:<30}binds {count:>2} of {len(summary)} features")
    out.append("")
    out.append("  EVERY cohort figure above rests on a MODELLED formation rate,")
    out.append("  not a measured one. What to measure next:")
    for chunk in _wrap(NEXT_MEASUREMENT, width - 6):
        out.append("    " + chunk)
    out.append("")
    return "\n".join(out)


# ==========================================================================
# (5) THE THREE-BUCKET CLASSIFIER
# ==========================================================================

#: The ONLY legitimate members of GENUINELY_UNAVAILABLE today, each with the
#: window in which it applies and the argument for its membership. A node is
#: in this bucket because a PRIMITIVE cannot be obtained -- never because no
#: provider publishes the derived metric.
UNAVAILABLE_REGISTER: dict[str, dict[str, Any]] = {
    "price_dead_company": {
        "applies": "entity_and_window",
        "from": None,
        "until": None,
        "test": ("pit_price_bar has no row for the listing in the window AND no "
                 "allowed vendor carries one. An unresolved TICKER is not this: "
                 "identity failures are repairable and belong to pit_identity."),
        "why": "The bars were never recorded, or were purged when the line died.",
    },
    "option_quote_pre_archive": {
        "applies": "date_window",
        "from": None,
        "until": "2026-09-20",
        "test": "as_of < 2026-09-20, the first day of the option archive.",
        "why": ("An option chain is a snapshot of a moment. Nobody sells the "
                "moment back afterwards, and it cannot be reconstructed from "
                "anything that was kept."),
    },
    "analyst_gpi_historical": {
        "applies": "always",
        "from": None,
        "until": None,
        "test": "any as_of: no historical analyst GPI series was ever written.",
        "why": ("Not unpublished -- never generated. Authoring it now and dating "
                "it historically would be hindsight wearing a timestamp, which "
                "is the single thing a point-in-time store exists to prevent."),
    },
    "never_tagged_fact": {
        "applies": "fact_conditional",
        "from": None,
        "until": None,
        "test": ("absent from every rung of EVERY ladder version, AND not "
                 "derivable from facts that are present. A fact the CURRENT "
                 "ladder misses fails this test: concept_ladder_v2 moved "
                 "total_debt from 15.9% to 37.9% by widening the ladder, which "
                 "proves those rows were never genuinely unavailable."),
        "why": ("The filer did not tag it. pit_policy.absence_limits() records "
                "that the store cannot yet separate never_tagged from "
                "tagged_zero from company_had_none -- 159,522 empty-value rows "
                "were counted and discarded at ingest -- so membership here is "
                "asserted per fact, never per concept."),
    },
}


def unavailable_admission_test() -> tuple[str, ...]:
    """The four questions any new GENUINELY_UNAVAILABLE claim must survive.

    Written down because the bucket's whole value is that it is small, and a
    bucket nobody has to argue their way into stops being small.
    """
    return (
        "1. Is the thing a PRIMITIVE? A derived metric no provider publishes is "
        "not unavailable -- computing it is the product.",
        "2. Has every ladder version been tried, and every allowed source? "
        "concept_ladder_v2 found 2.4x the total_debt v1 found, from the SAME "
        "loaded store, by changing nothing but the ladder's shape.",
        "3. Can it be legitimately RECONSTRUCTED from primitives that are "
        "present? Reconstruction is derivation and belongs in the graph.",
        "4. Would obtaining it require hindsight? If the only way to get it is "
        "to author it now and date it historically, it is unavailable AND must "
        "stay that way.",
    )


def classify(node_key: str, as_of: str = "",
             *, obtainable: Optional[Mapping[str, bool]] = None) -> dict[str, Any]:
    """Which of the three buckets `node_key` is in at `as_of`, and WHY.

    The rule that matters, stated once:

        A node is GENUINELY_UNAVAILABLE only if a required PRIMITIVE cannot be
        obtained from any source. It is NEVER genuinely unavailable merely
        because no provider publishes that derived metric.

    So `cohort_mean_ebitda` is SHAFFER_DERIVED, permanently, no matter how
    many vendor catalogues one searches. `option_quote_pre_archive` is
    GENUINELY_UNAVAILABLE before 2026-09-20 and a SOURCE_PRIMITIVE from it.

    `obtainable` lets a caller assert per-primitive facts for one entity or
    window (for instance, that this listing's price really is dead). Only keys
    in UNAVAILABLE_REGISTER may be asserted False; asserting that an ordinary
    primitive is unobtainable is refused, because a missing ordinary primitive
    is a coverage number, not a category.
    """
    item = node(node_key)
    day = str(as_of)[:10] if as_of else ""
    asserted = dict(obtainable or {})
    for key in asserted:
        if key not in UNAVAILABLE_REGISTER:
            raise ValueError(
                f"{key!r} may not be asserted unobtainable: it is not in "
                f"UNAVAILABLE_REGISTER. A primitive that merely fails to resolve "
                f"for some filers has a COVERAGE number, not a bucket. See "
                f"unavailable_admission_test().")

    def blocked(primitive_key: str) -> Optional[str]:
        """Why this primitive cannot be obtained at `day`, or None."""
        entry = UNAVAILABLE_REGISTER.get(primitive_key)
        if entry is None:
            return None
        if asserted.get(primitive_key) is True:
            return None
        applies = entry["applies"]
        if applies == "always":
            return entry["why"]
        if applies == "date_window":
            until = entry.get("until")
            if until and day and day < until:
                return f"{entry['why']} (as_of {day} precedes {until})"
            if until and not day:
                return f"{entry['why']} (no as_of given; the window is decided by date)"
            return None
        # entity_and_window / fact_conditional: only on an explicit assertion.
        if asserted.get(primitive_key) is False:
            return entry["why"]
        return None

    if item.kind == KIND_UNAVAILABLE:
        reason = blocked(node_key)
        if reason:
            return {"node": node_key, "as_of": day, "bucket": KIND_UNAVAILABLE,
                    "reason": reason, "blocking_primitives": [node_key],
                    "register": UNAVAILABLE_REGISTER.get(node_key, {})}
        return {"node": node_key, "as_of": day, "bucket": KIND_SOURCE,
                "reason": ("registered as conditionally unavailable, but the "
                           "condition does not hold here -- it is an ordinary "
                           "source primitive at this as_of"),
                "blocking_primitives": []}

    if item.is_primitive:
        return {"node": node_key, "as_of": day, "bucket": KIND_SOURCE,
                "reason": f"a fact obtained from {item.source}",
                "blocking_primitives": []}

    blocking: list[str] = []
    reasons: list[str] = []
    for ref in leaves(node_key):
        why = blocked(ref.key)
        if why:
            blocking.append(ref.key)
            reasons.append(f"{ref.key}: {why}")
    if blocking:
        return {
            "node": node_key, "as_of": day, "bucket": KIND_UNAVAILABLE,
            "reason": ("a REQUIRED PRIMITIVE cannot be obtained -- "
                       + "; ".join(reasons)),
            "blocking_primitives": sorted(set(blocking)),
        }
    return {
        "node": node_key, "as_of": day, "bucket": KIND_DERIVED,
        "reason": ("we compute this. No provider publishes it and none needs "
                   "to: every required primitive is obtainable, so any absence "
                   "is a coverage number on a named leaf, not a category."),
        "blocking_primitives": [],
        "leaves": [ref.leaf_id for ref in leaves(node_key)],
    }


# ==========================================================================
# (6) THE TRACE
# ==========================================================================

@dataclass
class TraceStep:
    """One node's appearance in an inspectable walk from facts to score."""

    key: str
    kind: str
    scope: str
    value: Any
    unit: str
    formula: str
    question: str
    inputs: tuple[tuple[str, Any], ...] = ()
    source: str = ""
    availability: str = ""
    note: str = ""


def _format_value(value: Any, unit: str) -> str:
    """One value, rendered for a human who will check the arithmetic."""
    if value is None:
        return "unavailable"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return f"[{len(value)} values]"
    number = float(value)
    if unit == "usd":
        if abs(number) >= 1e9:
            return f"${number / 1e9:,.3f}bn"
        if abs(number) >= 1e6:
            return f"${number / 1e6:,.1f}m"
        return f"${number:,.2f}"
    if unit == "pct":
        return f"{number:+.3f}%"
    if unit == "ratio":
        if abs(number) < 3.0:
            return f"{number:.4f}  ({number * 100:+.2f}%)"
        return f"{number:.4f}x"
    if unit == "score":
        return f"{number:+.3f}"
    if unit == "count":
        return f"{number:,.0f}"
    if unit == "index":
        return f"{number:.3f}"
    return f"{number:,.4f}"


def _edge_value(edge: Input, values: Mapping[str, Any]) -> Any:
    """One edge's value for the trace.

    A per-member edge has NO single value -- it is one value per cohort member
    -- so it must never fall back to the target company's own number. Showing
    the company's EV/EBITDA next to "ev_ebitda[per peer]" would read as though
    the benchmark had been built from the company being benchmarked.
    """
    if edge.leaf_id in values:
        return values[edge.leaf_id]
    if edge.per_member:
        return "(one per cohort member; not a single value)"
    return values.get(edge.key)


def trace(node_key: str,
          values: Mapping[str, Any],
          *,
          start: str = "",
          also: Sequence[str] = (),
          only: Sequence[str] = (),
          availability: Optional[Mapping[str, str]] = None) -> list[TraceStep]:
    """An inspectable walk of every node from raw facts to `node_key`.

    This is the thing the owner asked to be able to open: for each node its
    value, its inputs, the exact formula, its source or provenance, and its
    availability. It COMPUTES NOTHING -- every value is supplied by the caller,
    which is what keeps this module declarative and keeps a rendering bug from
    ever becoming a scoring bug.

    `start` restricts the walk to the nodes between two named endpoints, so
    "show me revenue to ShafferScore" is an exact request.

    `also` adds nodes (with their ancestors) that are not on the path but that
    a reader wants beside it -- the sector P/E and the EBITDA acceleration feed
    nothing in the score chain and are still two of the first things anyone
    asks about. Everything stays in one topological order, so the walk still
    reads strictly downhill from facts.

    `only` narrows the EMITTED steps to a named set while keeping them in
    that same order. Use it to answer "show me these fifteen quantities"
    without reordering them into the order they were asked for -- but know
    that a narrowed trace has GAPS, and a gap is the one thing a trace is
    supposed not to have. The unnarrowed walk is the authority.
    """
    wanted: set[str] = set()
    if start:
        span = between(start, node_key)
        if not span:
            raise ValueError(f"no path from {start!r} to {node_key!r}")
        wanted.update(span)
    else:
        wanted.update(ancestors(node_key))
        wanted.add(node_key)
    for extra in also:
        wanted.update(ancestors(extra))
        wanted.add(extra)
    if only:
        for key in only:
            node(key)                      # refuse an unknown key, loudly
        wanted &= set(only)
    keys = tuple(k for k in topological_order() if k in wanted)
    marks = dict(availability or {})
    steps: list[TraceStep] = []
    for key in keys:
        item = node(key)
        edges = tuple((edge.leaf_id, _edge_value(edge, values)) for edge in item.inputs)
        steps.append(TraceStep(
            key=key,
            kind=item.kind,
            scope=item.scope,
            value=values.get(key),
            unit=item.unit,
            formula=item.formula,
            question=item.question,
            inputs=edges,
            source=item.source,
            availability=marks.get(key, "complete" if values.get(key) is not None
                                   else "unavailable"),
            note=item.note,
        ))
    return steps


def render_trace(steps: Sequence[TraceStep], *, title: str = "",
                 width: int = 78, show_questions: bool = True) -> str:
    """The walk as text a person can read top to bottom and argue with."""
    out: list[str] = []
    rule = "=" * width
    out.append(rule)
    out.append(title or "DERIVATION TRACE")
    out.append(rule)
    bucket_mark = {KIND_SOURCE: "SOURCE ", KIND_DERIVED: "DERIVED",
                   KIND_UNAVAILABLE: "NO-SRC "}
    for index, step in enumerate(steps, start=1):
        scope = "  [cohort]" if step.scope == SCOPE_COHORT else (
            "  [macro]" if step.scope == SCOPE_MACRO else "")
        out.append(f"{index:>3}. {step.key}{scope}")
        out.append(f"     {bucket_mark[step.kind]}  {_format_value(step.value, step.unit)}"
                   f"    [{step.availability}]")
        if show_questions:
            out.append(f"     asks: {step.question}")
        out.append(f"     {step.formula}")
        if step.kind == KIND_SOURCE:
            out.append(f"     from: {step.source}")
        elif step.inputs:
            rendered = ", ".join(
                f"{name}={_format_value(value, node(name.split('@')[0].split('[')[0]).unit)}"
                if value is not None else f"{name}=unavailable"
                for name, value in step.inputs)
            out.append(f"     inputs: {rendered}")
        out.append("")
    return "\n".join(out)


# -- The fixture -----------------------------------------------------------
# Realistic, internally consistent, and NOT read from the store: the point is
# that the shape is visible now, with the database untouched. Every number was
# computed with the production core's own statlib functions (percentile,
# percentile_rank_within, percentile_to_score, winsorized_mean, clamp) so the
# arithmetic a reader checks is the arithmetic the replay will perform.

FIXTURE_AS_OF = "2019-06-28"

#: The owner's own list, in their words: "revenue, EBITDA, EBITDA YoY, margin,
#: sector P50 and P75 EBITDA, the 50-75 cohort mean, company-versus-benchmark,
#: real revenue growth, P/E, sector P/E, the extreme-valuation penalty,
#: CompanyScore, SectorScore and ShafferScore." Rendered as a NARROWED trace;
#: the full 69-node walk is what `python pit_derive.py` prints.
OWNER_TRACE_NODES: tuple[str, ...] = (
    "revenue", "ebitda", "ebitda_growth", "ebitda_margin",
    "sector_ebitda_p50", "sector_ebitda_p75", "cohort_50_75_membership",
    "cohort_mean_ebitda", "cohort_mean_margin", "ebitda_excess_level",
    "ebitda_benchmark_gap", "margin_excess", "real_revenue_growth",
    "pe_ratio", "sector_pe", "cohort_mean_ev_ebitda", "implied_price",
    "valuation_gap", "valuation_score", "extreme_valuation_penalty",
    "company_score", "sector_score", "sector_overlay", "shaffer_score",
)

#: Nodes a reader wants beside the score chain even though they feed nothing
#: in it: the owner named every one of these in the trace they asked for.
TRACE_EXTRAS: tuple[str, ...] = (
    "ebitda_acceleration", "real_revenue_growth", "pe_ratio", "sector_pe",
    "ebitda_percentile", "ebitda_scale", "cohort_membership_flag",
    "ebitda_excess_level", "ebitda_benchmark_gap", "margin_excess",
    "extreme_valuation_penalty", "fcf_yield", "fcf_conversion",
    "interest_coverage", "ev_ebitda",
)

FIXTURE_NOTE = (
    "A mid-cap industrial, SIC 3559, at 2019-06-28. Peer set: 24 peers with a "
    "usable EV/EBITDA, of which the 50-75 EBITDA band holds 6. Every number "
    "was computed with the production core's own statlib functions and then "
    "frozen here: these are FIXTURE values, not store reads, and no database "
    "was opened to produce them.")

FIXTURE_VALUES: dict[str, Any] = {
    # ---- source primitives, at t
    "revenue": 4_182_000_000.0,
    "operating_income": 612_400_000.0,
    "depreciation_amortisation": 188_900_000.0,
    "net_income": 396_700_000.0,
    "total_debt": 1_540_000_000.0,
    "cash": 412_300_000.0,
    "total_assets": 6_884_000_000.0,
    "equity": 2_970_000_000.0,
    "operating_cash_flow": 701_000_000.0,
    "capex": 214_000_000.0,
    "interest_expense": 71_800_000.0,
    "shares_outstanding": 128_400_000.0,
    "price_raw": 74.60,
    "price_adjusted": 74.60,
    "sic": "3559",
    "cpi": 255.213,
    "treasury_yield": 2.01,
    # ---- source primitives, lagged
    "revenue@t-1y": 3_905_000_000.0,
    "revenue@t-2y": 3_742_000_000.0,
    "operating_income@t-1y": 548_000_000.0,
    "operating_income@t-2y": 505_000_000.0,
    "depreciation_amortisation@t-1y": 176_500_000.0,
    "depreciation_amortisation@t-2y": 170_000_000.0,
    "cpi@t-1y": 251.989,
    # ---- company-level derivations
    "ebitda": 801_300_000.0,
    "ebitda@t-1y": 724_500_000.0,
    "ebitda@t-2y": 675_000_000.0,
    "ebitda_margin": 0.19160688665710188,
    "ebitda_growth": 0.10600414078674948,
    "ebitda_growth@t-1y": 0.07333333333333333,
    "ebitda_acceleration": 0.03267080745341615,
    "revenue_growth": 0.0709346991037132,
    "revenue_growth@t-1y": 0.0435595938001069,
    "revenue_acceleration": 0.027375105303606297,
    "inflation": 0.01279420927103958,
    "real_revenue_growth": 0.05740602513369453,
    "free_cash_flow": 487_000_000.0,
    "market_cap": 9_578_640_000.0,
    "enterprise_value": 10_706_340_000.0,
    "net_debt": 1_127_700_000.0,
    "pe_ratio": 24.145802873708092,
    "ev_ebitda": 13.361213028828153,
    "fcf_yield": 0.05084229076361571,
    "net_debt_ebitda": 1.4073380756271059,
    "debt_market_cap": 0.16077438968371294,
    "interest_coverage": 8.529247910863509,
    "roa": 0.05762638001162115,
    "roe": 0.13356902356902357,
    "fcf_conversion": 0.6077623861225508,
    # ---- cohort-level derivations (ONE value per sector-date, shared)
    "peer_set": 24,
    "sector_ebitda_vector": 24,
    "sector_ebitda_p50": 461_500_000.0,
    "sector_ebitda_p75": 853_250_000.0,
    "cohort_50_75_membership": 6,
    "cohort_mean_ebitda": 644_666_666.6666666,
    "cohort_mean_margin": 0.1866506359552119,
    "cohort_mean_ev_ebitda": 11.85,
    "sector_pe": 18.9,
    "sector_growth": 1.8,
    "sector_roe": 0.1121,
    "sector_roa": 0.0498,
    "sector_debt_market_cap": 0.2437,
    # ---- company against cohort
    "ebitda_percentile": 0.7083333333333334,
    "cohort_membership_flag": True,
    "ebitda_excess_level": 156_633_333.33333334,
    "ebitda_benchmark_gap": 0.24296794208893502,
    "ebitda_scale": 1.7363001083423621,
    "ebitda_margin_rank": 0.75,
    "margin_excess": 0.004956250701889975,
    # ---- valuation chain
    "implied_enterprise_value": 9_495_405_000.0,
    "implied_equity_value": 8_367_705_000.0,
    "implied_price": 65.16904205607477,
    "valuation_gap": -0.12642034777379654,
    "valuation_score": -24.75870946883516,
    "extreme_valuation_penalty": 0.5253600859241487,
    # ---- scores
    "growth_score": 39.99999999999999,
    "profitability_score": 38.333333333333336,
    "debt_score": 16.666666666666657,
    "company_raw_score": 10.263182879132598,
    "company_score": 7.697387159349448,
    "sector_score": 28.92,
    "sector_overlay": 7.23,
    "shaffer_score": 14.927387159349449,
}


# ==========================================================================
# (7) THE INTERMEDIATE STORAGE PROBLEM -- specified, NOT applied
# ==========================================================================
#
# pit_feature has `raw_value` and `normalized_value` and nowhere else to put a
# number. So under the current schema the sector P50, the P75, the 50-75
# membership band and the cohort mean EBITDA are computed during the replay
# and DISCARDED -- each one a statistic over ~32 companies, thrown away and
# recomputed from scratch on the next run, and unavailable afterwards to
# anyone asking why a company scored what it scored.
#
# The shape question is the whole question. The cohort statistics are
# COHORT-LEVEL: one value per (as_of_date, rung, key), consumed by every
# member. The company statistics are company-level. Storing them in one table
# means storing each cohort number once per member.
#
# MEASURED IMPLICATION, from the real figures (39,038 peer sets over 165 dates,
# ~1,240,000 entity-dates):
#
#   cohort-level table        39,038 rows/run   (236.6 per date)
#   company-level table    1,240,000 rows/run
#   one denormalised table 1,240,000 rows/run, each carrying 11 cohort fields
#                          whose distinct-value count is 39,038
#                          -> repetition factor 1,240,000 / 39,038 = 31.8x
#   as extra pit_feature rows (one per cohort statistic per company)
#                          11 x 1,240,000 = 13,640,000 rows, on a table that
#                          carries 24 columns and an immutability trigger
#
# RECOMMENDATION: the split. Two tables, below. Reasons, in order of weight:
#
#   1. It is the truth about the data. A cohort statistic is a property of the
#      cohort. A schema that repeats it 31.8 times asserts 1,240,000 facts
#      where 39,038 exist, and a reader cannot tell from the rows whether two
#      copies that disagree are a bug or a real difference.
#   2. It joins naturally. pit_peer_set is ALREADY keyed
#      (as_of_date, rung, key, model_version, peer_set_version), so the cohort
#      table is a 1:1 extension of a table that exists, with the same key and
#      an FK to peer_set_id.
#   3. Size. ~5.5 MB against ~322 MB for the denormalised form and an
#      estimated ~2.4 GB for the pit_feature route, on a store whose WAL once
#      reached 9.3 GB and filled the disk.
#   4. Correction cost. Both tables are immutable-by-trigger like the rest of
#      the store, so a corrected P50 means new rows under a new replay_run_id.
#      39,038 of them, not 1,240,000.
#
# The price of the split is one join to read a company beside its benchmark.
# That is the right price.
#
# NOTHING IN THIS MODULE EXECUTES THE DDL. `ensure_schema` exists for another
# process to run LATER, when no agent is mid-write on the store.

PIT_INTERMEDIATE_DDL = """
-- =====================================================================
-- COHORT-LEVEL INTERMEDIATES  (one row per cohort per as-of, ~39,038/run)
--
-- Keyed exactly like pit_peer_set, because it IS pit_peer_set's statistics:
-- (as_of_date, rung, key, model_version, peer_set_version). replay_run_id
-- joins the key because pit_feature and pit_score both carry it and two runs
-- of a corrected model must coexist rather than collide.
-- =====================================================================
CREATE TABLE IF NOT EXISTS pit_cohort_stat (
    as_of_date              TEXT NOT NULL,
    rung                    TEXT NOT NULL,   -- sic4 | sic3 | sic2 | office
    key                     TEXT NOT NULL,
    model_version           TEXT NOT NULL,
    peer_set_version        TEXT NOT NULL,
    replay_run_id           INTEGER NOT NULL REFERENCES pit_replay_run(run_id),
    peer_set_id             INTEGER REFERENCES pit_peer_set(peer_set_id),
    ladder_version          TEXT NOT NULL,

    -- how many companies the statistics rest on, at each narrowing
    n_sector_peers          INTEGER NOT NULL,
    n_ebitda_resolved       INTEGER NOT NULL,
    n_cohort_members        INTEGER NOT NULL,
    n_ev_ebitda_used        INTEGER,

    -- the distribution
    ebitda_p50              REAL,
    ebitda_p75              REAL,

    -- the cohort itself
    cohort_mean_ebitda      REAL,
    cohort_mean_margin      REAL,
    cohort_mean_ev_ebitda   REAL,

    -- how the cohort was reached, and how much to trust it
    benchmark_level         TEXT,            -- Industry | Sector Fallback
    binding_leaf            TEXT,            -- the primitive that capped n_*
    source_coverage         REAL,            -- resolved primitives / required
    confidence              TEXT,            -- high | medium | low
    created_at              TEXT NOT NULL,

    UNIQUE (as_of_date, rung, key, model_version, peer_set_version, replay_run_id),
    CHECK (n_cohort_members <= n_ebitda_resolved),
    CHECK (n_ebitda_resolved <= n_sector_peers),
    CHECK (ebitda_p50 IS NULL OR ebitda_p75 IS NULL OR ebitda_p50 <= ebitda_p75)
);

CREATE INDEX IF NOT EXISTS idx_pit_cohort_stat_asof
    ON pit_cohort_stat (as_of_date, model_version, replay_run_id);
CREATE INDEX IF NOT EXISTS idx_pit_cohort_stat_peer_set
    ON pit_cohort_stat (peer_set_id);

CREATE TRIGGER IF NOT EXISTS trg_pit_cohort_stat_no_update
BEFORE UPDATE ON pit_cohort_stat
BEGIN
    SELECT RAISE(ABORT, 'pit_cohort_stat is immutable: re-run the replay instead');
END;

-- =====================================================================
-- COMPANY-LEVEL INTERMEDIATES  (one row per entity per as-of, ~1.24M/run)
--
-- ONLY what is genuinely per-company. The cohort numbers are reached by the
-- FK, never copied: copying them would write 1,240,000 rows to say 39,038
-- things.
-- =====================================================================
CREATE TABLE IF NOT EXISTS pit_company_intermediate (
    entity_id               INTEGER NOT NULL REFERENCES pit_entity(entity_id),
    as_of_date              TEXT NOT NULL,
    model_version           TEXT NOT NULL,
    replay_run_id           INTEGER NOT NULL REFERENCES pit_replay_run(run_id),
    peer_set_id             INTEGER REFERENCES pit_peer_set(peer_set_id),
    cohort_rung             TEXT,
    cohort_key              TEXT,

    company_ebitda          REAL,
    company_ebitda_percentile REAL,          -- rank inside the sector vector
    company_ebitda_margin   REAL,
    in_cohort_50_75         INTEGER NOT NULL DEFAULT 0,

    -- company against the benchmark: the pair of numbers the score turns on
    ebitda_excess_level     REAL,            -- company - cohort mean, dollars
    ebitda_benchmark_gap    REAL,            -- company / cohort mean - 1
    margin_excess           REAL,            -- company margin - cohort mean

    binding_leaf            TEXT,            -- which primitive capped this row
    source_coverage         REAL,
    confidence              TEXT,
    created_at              TEXT NOT NULL,

    UNIQUE (entity_id, as_of_date, model_version, replay_run_id),
    CHECK (in_cohort_50_75 IN (0, 1)),
    CHECK (company_ebitda_percentile IS NULL
           OR (company_ebitda_percentile >= 0.0 AND company_ebitda_percentile <= 1.0))
);

CREATE INDEX IF NOT EXISTS idx_pit_company_intermediate_asof
    ON pit_company_intermediate (as_of_date, model_version, replay_run_id);
CREATE INDEX IF NOT EXISTS idx_pit_company_intermediate_entity
    ON pit_company_intermediate (entity_id, as_of_date);

CREATE TRIGGER IF NOT EXISTS trg_pit_company_intermediate_no_update
BEFORE UPDATE ON pit_company_intermediate
BEGIN
    SELECT RAISE(ABORT, 'pit_company_intermediate is immutable: re-run the replay');
END;
"""


def ensure_schema(conn: Any) -> None:
    """Create the intermediate tables. FOR ANOTHER PROCESS TO CALL, LATER.

    NOTHING IN THIS MODULE CALLS THIS, and nothing in this module opens a
    connection to pass it. The store is being written to by another agent, and
    a reader that lingers blocks the WAL checkpointer -- which is how this
    project once put a 9.3 GB WAL on a volume with 13.4 GiB free.

    Idempotent: every statement is IF NOT EXISTS, so running it against a
    store that already has the tables is a no-op. It adds tables and never
    alters one, so it cannot disturb a concurrent write to pit_fact.
    """
    conn.executescript(PIT_INTERMEDIATE_DDL)
    conn.commit()


#: Real figures the row-count arithmetic rests on. Measured, not assumed.
PEER_SETS_TOTAL = 39_038
AS_OF_DATES_TOTAL = 165
ENTITY_DATES_TOTAL = 1_240_000

#: Cohort statistics that would be repeated on every member's row under the
#: denormalised alternative. Eleven, exactly as the owner enumerated them.
COHORT_FIELDS = (
    "n_sector_peers", "n_ebitda_resolved", "ebitda_p50", "ebitda_p75",
    "n_cohort_members", "cohort_mean_ebitda", "cohort_mean_margin",
    "cohort_mean_ev_ebitda", "benchmark_level", "source_coverage", "confidence",
)


def storage_recommendation() -> dict[str, Any]:
    """The shape recommendation, with the row-count arithmetic that decides it.

    Declarative: computes the comparison, applies nothing. Byte figures are
    ESTIMATES at ~140 bytes per cohort row and ~120 per company row and are
    labelled as such; the ROW counts are exact arithmetic on measured totals.
    """
    repetition = ENTITY_DATES_TOTAL / PEER_SETS_TOTAL
    cohort_rows = PEER_SETS_TOTAL
    company_rows = ENTITY_DATES_TOTAL
    feature_rows = len(COHORT_FIELDS) * ENTITY_DATES_TOTAL
    return {
        "recommendation": "split: pit_cohort_stat + pit_company_intermediate",
        "measured_inputs": {
            "peer_sets": PEER_SETS_TOTAL,
            "as_of_dates": AS_OF_DATES_TOTAL,
            "entity_dates": ENTITY_DATES_TOTAL,
            "peer_sets_per_date": round(PEER_SETS_TOTAL / AS_OF_DATES_TOTAL, 1),
            "mean_peer_set_size": round(repetition, 2),
        },
        "options": {
            "split_recommended": {
                "cohort_rows_per_run": cohort_rows,
                "company_rows_per_run": company_rows,
                "cohort_bytes_estimate": cohort_rows * 140,
                "company_bytes_estimate": company_rows * 120,
                "total_bytes_estimate": cohort_rows * 140 + company_rows * 120,
                "cost": "one join to read a company beside its benchmark",
            },
            "denormalised_per_company_blob": {
                "rows_per_run": company_rows,
                "redundant_copies": company_rows - cohort_rows,
                "repetition_factor": round(repetition, 2),
                "bytes_estimate": company_rows * 260,
                "objection": (f"asserts {company_rows:,} facts where {cohort_rows:,} "
                              f"exist; two disagreeing copies are indistinguishable "
                              f"from a real difference"),
            },
            "cohort_stats_as_pit_feature_rows": {
                "rows_per_run": feature_rows,
                "bytes_estimate": feature_rows * 180,
                "objection": ("pit_feature would be dominated by restated cohort "
                              "constants; its UNIQUE key is per ENTITY, so a "
                              "cohort statistic has no natural home in it"),
            },
        },
        "why": [
            "a cohort statistic is a property of the cohort, and the schema "
            "should say so",
            "pit_peer_set is already keyed (as_of_date, rung, key, "
            "model_version, peer_set_version) -- the cohort table is a 1:1 "
            "extension of a table that exists",
            f"{repetition:.1f}x fewer rows for the cohort half, and a corrected "
            f"P50 rewrites {cohort_rows:,} rows rather than {company_rows:,}",
            "both tables carry the store's immutability trigger, so a "
            "correction is a new replay_run_id, never an UPDATE",
        ],
        "not_applied": ("ensure_schema() is written for another process to run "
                        "later. This module executes no SQL and opens no "
                        "connection; a lingering reader blocks the WAL "
                        "checkpointer."),
    }


# ==========================================================================
# Demo: the coverage report and the trace, from constants only.
# ==========================================================================

def _demo() -> None:
    for day in MEASURED_DATES:
        marginals = primitive_availability(day)
        print(coverage_report(
            marginals,
            label=f"{day}  (base = {MEASURED_BASE[day]:,} peers with a usable "
                  f"non-stale Assets fact; total_debt on concept_ladder_v2)",
            measured_joints={k: v[day] for k, v in MEASURED_JOINTS.items()},
            lift=co_occurrence_lift(day)))
    print(render_trace(trace("shaffer_score", FIXTURE_VALUES, also=TRACE_EXTRAS),
                       title=(f"TRACE: raw facts -> ShafferScore at "
                              f"{FIXTURE_AS_OF}" + chr(10) + FIXTURE_NOTE)))


if __name__ == "__main__":
    _demo()
