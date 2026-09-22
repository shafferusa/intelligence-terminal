# candidate_equity_shaffer_v2 — design findings

**Status: CANDIDATE SPECIFICATION UNDER REVIEW. Not implemented. `equity_shaffer_v1` remains frozen,
promoted and reproducible.** Nothing here changes a production number.

Date: 2026-09-20. Five independent designers worked one block each against the real point-in-time
store; a sixth adversarially reviewed them and found arithmetic errors in their work, which are
corrected below.

**A limitation to state first, because it bounds everything else:** the review received only **two of
the five proposals** (EBITDA and Valuation) — the others exceeded the payload cap in the harness I
wrote. **Growth, Quality and Sector are therefore unreviewed.** Their findings below stand on their
own designer's evidence, not on a second opinion, and the review must be re-run before any of the
three is implemented.

---

## 1. The finding that changes the architecture

Your instinct — *"a giant mediocre company automatically beats a fantastic smaller company"* — is
correct, and the situation is worse than a tuning problem. It is a theorem.

```
EBITDAGap = (E_i − b) / |b|
```

Within a cohort, `b` is a constant. So `EBITDAGap` is a strictly increasing **affine** function of
`E_i`, and its within-cohort ranking is *identical* to the ranking of raw EBITDA dollars.

**Measured Spearman ρ(EBITDAGap, raw EBITDA dollars), as-of 2015-06-30:**

| Cohort | ρ |
|---|---|
| SIC 737x | 1.000000 |
| SIC 36xx | 1.000000 |
| SIC 28xx | 1.000000 |
| SIC 13xx | 1.000000 |
| SIC 35xx | 1.000000 |

Exactly 1.0 in every cohort, and unchanged under bands 40–70, 50–75 and 60–80 alike. **The benchmark
contributes zero ranking information** — it sets the zero point and the unit, nothing more. And that
ranking is size: ρ(EBITDAGap, log Assets) runs 0.487 to 0.859 across cohorts.

The consequence, worked in SIC 35xx FY2014 (benchmark $81,472,289):

| Company | EBITDA | Margin | EBITDAGap |
|---|---|---|---|
| Apple | $60,449,000,000 | 33.1% | **+740.96** |
| entity 2020 | $309,903,000 | **33.3%** | **+2.93** |

A **261× advantage for being 195× bigger**, awarded against a company with a *higher* margin. Note the
range too: a profitable company's entire downside is bounded at −1.00 (EBITDA = 0), so the factor spans
[−1, +741]. Once EBITDA goes negative the floor disappears as well — −248.80 was observed in SIC 28xx.

**No monotone transform fixes this.** Log, signed-log, tanh, arcsinh, winsorised ratio — all preserve
rank, and rank is the problem. The only fix is to change *what is being compared*, from dollars to an
intensity.

**Recommendation — keep your benchmark, change the quantity.** The 50–75 band mean is a good location
estimator and stays as the zero point. What must go is *dividing by it*: a benchmark is a location, not
a unit. Divide instead by a robust cohort dispersion (IQR), and split the gap:

```
GapIntensity  = EBITDA / Assets   vs the cohort           weight 0.60–0.70
GapScale      = EBITDA dollars    vs the cohort           weight 0.30–0.40
```

Measured on your own stated failure case (giant, 10.3% margin vs small, 26.8% margin):

| Scoring | Giant | Small | Winner |
|---|---|---|---|
| Dollar gap only | +59.8 | +30.9 | **Giant** ← your nightmare |
| Intensity only | +4.1 | +42.2 | Small |
| Blend 0.30/0.70 | +20.8 | +38.8 | Small, size tilt retained |

The ordering flips at `w_intensity = 0.431`. So an intensity weight of roughly **0.45 or higher is
implied by your own sentence** — it is a requirement, not a fitted parameter. *(The reviewer notes this
crossover was solved on the gap term alone, without the margin term in the blend, so treat 0.60–0.70 as
the hypothesis and 0.45 as the floor.)*

---

## 2. The inflation adjustment, as written, does nothing

This is the most surprising result, and it matters because you feel strongly about it.

Under v1's normalisation — `percentile_rank_within` → `percentile_to_score` — subtracting a deflator
that is **common to the cross-section** cannot change any sub-score. Rank is invariant to a shift.
Run in this project's own `statlib`, 7 companies, π = 9.06% (the June-2022 CPI peak):

| Nominal | − π | ÷ (1+π) | Score (nominal) | Score (− π) | Score (÷) |
|---|---|---|---|---|---|
| +32.00% | +22.94% | +21.03% | +100.000 | +100.000 | +100.000 |
| +18.00% | +8.94% | +8.20% | +66.667 | +66.667 | +66.667 |
| +12.00% | +2.94% | +2.70% | +33.333 | +33.333 | +33.333 |
| +9.00% | **−0.06%** | −0.06% | 0.000 | 0.000 | 0.000 |
| +4.00% | −5.06% | −4.64% | −33.333 | −33.333 | −33.333 |

Identical, to the digit. A company growing +4% into 9% inflation — destroying real value — scores
−33.3 either way, and a company at +9% scores exactly 0 either way. **The deflator is inert.**

**Recommendation:** to make inflation matter, real growth cannot be scored by peer rank alone. Score it
as a blend of a **level-anchored bounded transform** (zero at zero *real* growth, so the sign of the
score is the sign of real value creation) and the peer rank, with the peer weight ramping with cohort
size. That is the only construction in which "8% growth at 6% inflation is barely positive" survives
into the score.

**And the deflation window must match the growth window.** Deflating by trailing CPI at the as-of date
is wrong and the error is *correlated with fiscal calendar*: at as-of 2021-03-31, a December filer's
growth window (CPI YoY 1.36%) and a June filer's (0.65%) both get deflated by the March YoY of 2.62%,
under-deflating one by 1.26pp and the other by 1.97pp. That systematically mis-scores the 28% of the
universe that is not a December filer.

---

## 3. Valuation: the log trap is real, and the fix has a curve you can inspect

Confirmed numerically: `ln(30/20) = ln(150/100) = 0.405465`. A linear-in-log penalty charges **17.61
points for both moves** — ratio exactly 1.00× against your requirement of >1.

The proposed transform is linear-in-log near parity and **quadratic beyond a knot at 2× the peer
median**:

```
u = ln(m_i / m_peer)
cheap  (u ≤ 0):  S = +C · tanh((A/C)·(−u))            saturating
rich   (u > 0):  S = −min(100, A·u + B·max(0, u−θ)²)  accelerating

A = 25 points per log-unit,  θ = ln 2,  C = 60,
B = 16.3825  (not free — solved so the −100 floor lands exactly at 10× the peer median)
```

On that curve: **20→30 costs 10.14 points, 100→150 costs 25.00 — a ratio of 2.47×.** Your requirement,
delivered.

The asymmetry is deliberate: cheapness saturates because extreme cheapness is more often distress than
opportunity; expensiveness accelerates because that is where the risk lives.

**Two further defects, both severe:**

**v1's current valuation transform is not merely flat in the tail — it is inverted.** `100·tanh(2·gap)`
charges 58.28 points for 20→30 and **1.78 points** for 100→150: the second move costs one-thirty-third
of the first, against a requirement that it cost more. It also saturates ~19% of a realistic
cross-section at |V| > 90, where the factor is a binary with no ranking content.

**Capping |P/E| ranks the worst loss-makers as the cheapest securities in the market.** Taking the
absolute value of a ratio whose denominator crosses zero maps (−∞, 0) onto (0, +∞) **reversed**:

| Company | Price | EPS | P/E | Scored as |
|---|---|---|---|---|
| A | $10 | −$0.01 | −1000 | most expensive in the universe |
| B | $10 | −$5.00 | −2 | **cheaper than a 3× utility** |

B is destroying half its market value a year and wins the factor. **Rule: a price multiple exists only
where its denominator is strictly positive.** Otherwise the leg is unavailable and the signed yield goes
to the ML layer as a research candidate.

**The two heaviest legs die together.** Measured in the live store, FY2014, n = 2,756: NI ≤ 0 for
**49.2%**, EBITDA ≤ 0 for **37.8%**, *both* for **36.5%**. And 96.6% of EBITDA-negative firms are also
NI-negative — the failures don't diversify. Unfixed, "Valuation, 25% of the score" silently becomes
"FCF yield" for over a third of the universe. The fix is a named fallback to a relative **sales**
multiple through the identical hinge, with `earnings_slot_basis` recorded on the row.

---

## 4. The sector score: as written, the cap does all the work

Four terms each on [−100, +100], summed, then clipped to ±15. Simulated over 200,000 draws:
**93.4% of sector-months land outside the cap** and clip to exactly +15 or exactly −15. Two sectors
summing to +178 and +89 — very different — both score +15.

**Fix:** blend to [−100, +100] with weights summing to 1, *then* scale once.

**And v1's sector score is itself the failure mode you want to eliminate.** Its growth factor is a
*price* return, and its Debt/MarketCap factor improves when prices rise (debt 400 / cap 1000 = 0.40;
prices double → 0.20, and lower is better). Together those are **65% of v1's sector raw score, and both
improve purely because prices went up** — which is exactly "AI sector prices doubled, therefore +15".

**v2's sector growth must be fundamental** — real revenue and real EBITDA growth of the constituents.
Price enters in exactly one place, the numerator of ValuationExcess, with a negative sign.

---

## 5. The 0.85, settled

Your two readings are **algebraically identical on complete data** — `0.35E + 0.25V + 0.15G + 0.10Q` is
exactly `0.85 × (0.412E + 0.294V + 0.176G + 0.118Q)`. They differ *only* when a block is missing, which
is why the question surfaced at all.

The naive rule breaks the range. Drop Valuation: survivors renormalise to 1.0, the company score
reaches **±100**, and Shaffer Score reaches **±115**. Worse, that happens for the *majority* — 56.6%
lack the EBITDA block at annual frequency — so most companies would be scored on a wider scale than
the minority.

```
COMPANY_SCALE_V2 = 0.85
used[k] = COMPANY_SCALE_V2 × W[k] / Σ W[available]
CompanyScore = clamp(Σ used[k]·block[k], −85, +85)
MIN_BLOCK_WEIGHT = 0.40   # below this, emit NO score rather than a thin one
```

The rejected alternative — letting a missing block contribute zero — injects a sector tilt: the
quality-only population would cap at +10 while the full population caps at +85, so a perfect company in
the thin group could never outrank a median company in the full group. Availability is **not** random
(EBITDA resolves for 19.4% of financials vs 56.2% of non-financials), so zero-filling biases the rank.

---

## 6. Effective weights — the double counting, quantified

| # | Quantity | Effective | Share |
|---|---|---|---|
| 1 | EBITDAGap | .1225 | 14.4% |
| 2 | P/E | .1125 | 13.2% |
| 3 | EV/EBITDA | .1000 | 11.8% |
| 4 | EBITDAGrowth | .0875 | 10.3% |
| 5 | RealRevenueGrowth | .0750 | 8.8% |
| 6= | EBITDAMargin | .0700 | 8.2% |
| 6= | EBITDAAcceleration | .0700 | 8.2% |
| 8 | RealEBITDAGrowth | .0450 | 5.3% |

**EBITDA growth totals 0.1325 = 15.6% of the company score — the single largest input in the model —
and it is invisible as such because it is split across two blocks.** It carries 1.77× the weight of
revenue growth. The "Real" prefix cannot separate the two copies, because §2 proves the deflator is
rank-preserving inside a cohort.

**Recommendation:** every EBITDA-growth term lives **once**. Either the EBITDA block keeps it and the
Growth block drops `RealEBITDAGrowth` (making Growth the *non-EBITDA* growth block, which is what a
second block is for), or the reverse. Not both.

---

## 7. A correction to what I told you about coverage

I reported EBITDA as assemblable for "~85% of the cross-section, with OperatingIncomeLoss binding."
**Measured on the real store at as-of 2015-06-30, it is 43.4%** — 3,425 of 7,883 entities.

The binding constraint is not OperatingIncomeLoss (73% alone). It is the **join**: operating income and
D&A at a *matched period end*, both passing staleness. My 85% figure was an inclusion–exclusion bound on
two marginals, not a measured intersection.

**TTM via YTD-differencing is the fix** — `FY_prior + YTD_current − YTD_prior`, with a ±20-day window on
the prior-year period end because fiscal quarter-ends drift. That raises both-components coverage to
**87.4%**, though conditionally on companies that already have an annual EBITDA, so the unconditional
figure is unmeasured and bounded below by 43.4%.

*(Do not build TTM from four quarterly facts: 96.9% have four quarterly OI values and 94.8% have four
quarterly D&A values, but only **25.2%** have them at the same four quarter-ends, because D&A is a
cash-flow add-back reported year-to-date.)*

Financials are structurally excluded either way: EBITDA assembles for 18.1% of SIC 60–67, and **1.2% of
depository institutions**.

One consequence deserves emphasis: **EBITDA Strength is the only 25%+ block of v2 that can be replayed
end to end today.** It needs no shares outstanding, no price, no market cap, no inflation. Every other
block waits on the market-cap chain.

---

## 8. Errors the review caught in the designers' own work

Recorded because they show the review was real:

- **Acceleration had a biased denominator.** `(E_t − E_{t−4q})/A_{t−4q} − (E_{t−4q} − E_{t−8q})/A_{t−8q}`
  divides the two differences by *different* asset bases, so any company whose assets grow acquires a
  systematic negative acceleration. Correct form is the clean second difference over one common base:
  `(E_t − 2E_{t−4q} + E_{t−8q}) / A_{t−8q}`.
- **Acceleration's noise was understated by 22%.** The two differences share a term with opposite signs,
  so the variance ratio is 3×, not 2× — sd 1.73×, not 1.41×.
- **A calibration table was internally inconsistent.** The claimed pin rates require values 3,985 IQRs
  from the benchmark; the transform's algebra contradicts the table.
- **v1's valuation gap divides by market cap, not EV**, so the comparison table holds only at zero net
  debt. The qualitative conclusion survives.

---

## 9. Open for your decision

1. **Confirm 0.85** is a deliberate scale leaving ±15 for the sector, not a coincidence of four weights.
2. **Period basis — TTM or annual?** It must be one answer for all flow concepts, or the same company
   carries two EBITDAs. TTM is the single highest-value coverage change available.
3. **May `EBITDAGap` become an efficiency measure?** Satisfying your giant-vs-small sentence *requires*
   converting most of it to EBITDA per dollar of assets. That is a theorem, not a preference.
4. **Which copy of EBITDA growth survives** — the EBITDA block's or the Growth block's?
5. **Relative or absolute valuation anchor?** As specified, 150× in a sector whose median is 100× scores
   only mildly expensive, because the knot sits at twice the *peer* median. Your "20× → 30× vs 100× →
   150×" example reads as an *absolute-level* statement.
6. **Growth as an amount rather than a rate?** 34% of the universe has EBITDA ≤ 0, and a growth *rate*
   on that base turns −$50k → +$10m into +20,100% while a real $50m → $100m doubling reads +100%.
7. **Re-run the review with all five proposals** before Growth, Quality or Sector is implemented.

---

# ADDENDUM — settled 2026-09-21

## S1. §9.3 is settled: EBITDA efficiency, not a dollar gap

`EBITDAGap` becomes an **excess-EBITDA / efficiency** measure. The owner's 50th–75th percentile
sector cohort is **retained as Shaffer's identity** — what changes is the quantity compared, not the
cohort:

```
C_s               = sector peers whose EBITDA falls in the 50th–75th percentile
M_s               = mean( EBITDA_j / Revenue_j )  for j in C_s
ExpectedEBITDA_i  = Revenue_i × M_s
EBITDAExcess_i    = (EBITDA_i − ExpectedEBITDA_i) / Revenue_i   ≡  Margin_i − M_s
```

The `ExpectedEBITDA` intermediate is kept deliberately, because it is what makes the explanation
sayable: *"this company generates $X more EBITDA than a comparably efficient company at its revenue
scale would be expected to generate."*

**But `EBITDAExcess` must NOT be percentile-ranked afterwards.** Proven in `statlib`: `excess ==
margin − M_s` exactly, and the rank-normalised scores are identical to the digit. Ranked, the
cohort benchmark contributes nothing and `.30 Excess + .20 Margin` becomes 0.50 on one quantity.

Both terms survive **only because they are normalised differently**, and they then answer genuinely
different questions:

| Factor | Normalisation | Question |
|---|---|---|
| `EBITDAMarginRank` | PERCENTILE_RANK | How efficient is this business relative to the whole sector? |
| `EBITDAExcessLevel` | BENCHMARK_ANCHORED | Does it clear the bar set by the 50–75 cohort, and by how much? |

A company can rank in the 70th percentile of a terrible sector while having poor absolute economics;
another can have a high absolute margin in an exceptionally profitable industry and rank mid-pack.
Shaffer should know both.

`EBITDAScale` is retained as a small, explicit size term. Note the log is **inert** under a rank
(`S(EBITDA) ≡ S(log1p(EBITDA))`, measured) and `log1p` is undefined for the 37.8% with EBITDA ≤ 0 —
so rank the raw value and keep the weight small (starting hypothesis 0.05).

## S2. The foundational rule: normalisation is typed

**Percentile normalisation can erase economically meaningful structure.** For a pure within-cohort
rank transform S, any strictly monotone f and any cohort-wide constant c give

```
S(x) = S(f(x))        and        S(x) = S(x − c)
```

Three factors in this project were designed on purpose and destroyed by this, each measured:

| Intended factor | Collapses to | Measured |
|---|---|---|
| `RevenueGrowth − π` | `RevenueGrowth` | identical scores to the digit |
| `Margin − M_s` | `Margin` | identical scores to the digit |
| `log(EBITDA)` | `EBITDA` | identical scores to the digit |

**Every factor therefore carries a `normalization_type`:** `PERCENTILE_RANK`, `ZERO_ANCHORED`,
`BENCHMARK_ANCHORED`, `HISTORICAL_Z`, and eventually `ML_CALIBRATED`. `pit_feature.norm_method`
already exists to hold it.

- **ZERO_ANCHORED** — `S = 100·tanh(x / g)`. For real growth: 0% real growth scores exactly 0,
  +10% is genuinely bullish, −10% genuinely bearish. **This is the construction in which the
  inflation adjustment finally does something.**
- **BENCHMARK_ANCHORED** — `S = 100·tanh((x − benchmark) / k)`. Cohort parity → 0, above → positive,
  below → negative, extremes saturate. **This is the construction in which the 50–75 cohort matters.**

A scale parameter estimated from the cohort **at the as-of date** is point-in-time legitimate; one
estimated from full history is not.

## S3. The redundancy guard

Before any new factor is admitted, run `Spearman(new, existing)` — **within cohort**, since these
collapses are within-cohort phenomena that a pooled correlation can hide — and test separately for
*exact* rank equivalence, which is a structural fact rather than a statistical one. On ρ ≈ 1 (or
≈ −1, which is equally redundant), flag:

```
REDUNDANT UNDER CURRENT NORMALIZATION
```

This would have caught all three cases above immediately. It is implemented in
`pit_normalization.py` and its acceptance test is precisely those three cases.

## S4. The regression challenger, and the test that decides whether to build it

```
log(EBITDA_i) = α_s + β_s·log(Revenue_i) + ε_i        EBITDAResidual_i = ε_i
```

The whole value of this challenger hinges on one measurable quantity. **If β_s ≈ 1 then
ε ≈ log(EBITDA/Revenue) − α_s — rank-identical to margin again**, and the residual is margin in a
costume. It carries new information only if returns to scale are materially non-unit.

Measure before allocating effort: median β_s, interquartile range, standard error, stability over
time, the proportion of sectors whose confidence interval excludes 1, and the out-of-sample
predictive value of the residual versus plain margin.

This stays an **ML/research challenger**. It does not silently replace the human-designed Shaffer
EBITDA block.

## S5. Selection effects are first-class, not footnotes

`log(EBITDA)` requires EBITDA > 0, excluding **37.8%** of the universe. `EBITDAExcess` requires
Revenue, a further **13.2%** hole on top of EBITDA's own — a hole the dollar gap did not have.

The Revenue cost is accepted for the candidate research model, because the feature now measures what
is meant. But the excluded observations are **not discarded**: the EBITDA-only information is
preserved so the historical research can compare

```
Model_broad_coverage    vs    Model_richer_efficiency
```

The richer model may win conditionally and lose overall on missingness. **That comparison is exactly
what the point-in-time research exists to settle**, and neither answer is assumed.

## S6. Consequence for the architecture

Shaffer v2 is not "more factors." The mapping of economic information into [−100, +100] may matter
nearly as much as the choice of financial variables. Normalisation type is part of the model
specification, is recorded per factor, and is versioned with it.

---

# ADDENDUM 2 — competing specifications, settled by evidence

## C1. The rule

Where a design question has two defensible answers and the history can separate
them, **both are specified and the evidence decides**. That is what the point-in-time
foundation exists for. A judgement call spent here is a judgement call wasted.

This is not a licence to build everything: it applies where (i) both forms are
economically coherent, (ii) they are cheap to compute from the same inputs, and
(iii) a walk-forward comparison can actually distinguish them. Where those fail,
someone still has to choose.

## C2. Sector growth — is "too much" bad?

**Spec A — PEAKED.** Sector fundamental growth has an inverted-U response: good,
then overheated. Requires a new `normalization_type` (`PEAKED`) and a peak
location `g*`.

**Spec B — TWO MONOTONE TERMS.** Growth is monotonically good; what is bad is
*price* growth in excess of *fundamental* growth. Needs no new machinery — it is
the `ValuationExcess` term already in the sector spec.

Both are built. The comparison is run per sector and per regime, because the
answer may differ between them — and that difference would itself be a finding.

**Two methodological requirements, or the comparison is worthless:**

1. **`g*` is a free parameter and must be fit INSIDE the training fold only.**
   Fitting the peak on the same data that evaluates it manufactures an
   inverted-U in noise. Spec A starts with one more degree of freedom than Spec B
   and must clear a correspondingly higher bar, not merely a higher score.
2. **Run the redundancy guard between them.** If sector growth looks hump-shaped
   *because* high growth co-occurs with valuation excess, then A and B are two
   encodings of one phenomenon and the guard should say so. A "win" for A that is
   really B in disguise is the exact failure the guard was built to prevent.

## C3. Valuation — conditional or parallel?

**Spec P — PARALLEL.** Valuation is one of four weighted blocks (v1's shape).

**Spec C — CONDITIONAL.** The valuation anchor is adjusted for what quality and
growth justify, so the score measures *mispricing* rather than cheapness:
`S = f(Quality, Sector) − g(price)`.

Spec C gives "Great Company ≠ Great Stock At This Price" structurally. Spec P
gives it only if the valuation weight is large enough to dominate — a fragile way
to obtain a property that ought to be architectural. Both are computable from the
same inputs, so both are built.

## C4. EBITDA — broad coverage or richer efficiency?

Already specified in S5, evaluated on the three-layer protocol, plus the fourth
measurement: the predictive value of the names **only the broad model can see**.

## C5. What this costs

Three dual specifications is roughly 1.6x the replay compute, not 2x — the
expensive stages (fact selection, peer sets, price paths) are shared, and only the
scoring arithmetic differs. That is affordable. What is not affordable is
multiplying dual specs without limit: each one doubles the multiple-testing
surface, and the discovery/validation/holdout discipline has to absorb it. Three
is the budget until evidence retires one.
