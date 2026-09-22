# The Shaffer confidence framework

**Status: SPECIFICATION, 2026-09-21. Three of the four dimensions already exist
under separate vocabularies; one is new.**

The governing separation:

```
What do we think?          →  the Shaffer Score
How good is the            →  the confidence framework
information behind it?
```

These stay separate throughout the system. A +70 score with mediocre evidence
and a +40 score with excellent evidence are **different pieces of information**,
and the system must not silently decide that the first becomes +35.

---

## 1. Four dimensions, and why they cannot be one number

```
Confidence = { Data, Score, Forecast, Hedge }
```

Not a master percentage. The four have genuinely **different types**, so
collapsing them is a type error rather than a loss of detail:

**Canonical names** — existing objects map into these; no new vocabulary is invented:

```
data_quality          score_completeness          forecast_evidence          hedge_evidence
```

| Dimension | Answers | Type | Status |
|---|---|---|---|
| **Data** | How trustworthy are the inputs behind this factor? | continuous [0,1], **per factor** | **NEW** |
| **Score** | How complete and comparable is this score? | structural/categorical — signature, full/partial | **BUILT** (`pit_score_signature`) |
| **Forecast** | Does the evidence support using this score at this horizon? | per-horizon verdict — SUFFICIENT / INSUFFICIENT_EVIDENCE | **BUILT** (`pit_invariants.evidence_status`) |
| **Hedge** | Is the hedge recommendation historically validated? | temporal — prospective-only before the archive start | **BUILT** (`pit_hedge_snapshot.research_status`) |

A single "Confidence: 82%" would average a probability, a category, a per-horizon
verdict and a date. What the user sees instead:

```
Shaffer Score:          +61
Data quality:           High
Score completeness:     Full
3M forecast evidence:   Strong
12M forecast evidence:  Insufficient
Hedge evidence:         Prospective only
```

The work is smaller than it looks — three of four exist — but the vocabularies
must be **reconciled**, or the project ends up with three parallel systems that
say the same thing in different words.

---

## 2. Data confidence — the new dimension

Per factor:

```
Q_i = f( SourceQuality, Freshness, PeerCoherence, PrimitiveCoverage )     Q ∈ [0,1]
```

**Value shrinkage, not weight shrinkage.** The effective factor is

```
F_effective = F_raw × Q
```

rather than a reduced pillar weight. Reasons:

- a **continuous** quality measure should not trigger a **discrete** signature
  change every time a debt rung slips;
- the signature stays stable, so comparability is preserved;
- degradation is continuous instead of a threshold jump;
- a damped factor becomes less bullish *and* less bearish, which is the correct
  behaviour for a noisy signal — it moves toward neutral, not toward a verdict.

**Store all three, never only the damped number:**

```
F_raw          what the arithmetic produced
Q              the quality multiplier, with its components
F_effective    F_raw × Q
```

Because the shrinkage itself is a hypothesis. `Q = 0.70` might belong at 0.85 or
0.40, and that must be **learnable** — which is impossible if only the product
was stored.

**Consequence to accept deliberately:** two rows with the same signature `VGPD`
can now carry very different information content. That is why `Q` is its own
column rather than folded into the value, and why
`P(Return | Score, Signature, Quality)` stays answerable.

---

## 3. The combination rule is NOT yet decided

`Q = DataQuality × PeerCoherence` is **not** adopted, pending measurement.

The concern: a weak debt rung and a thin, incoherent peer set are plausibly
symptoms of **one latent cause** — small, obscure, thinly-covered companies.
Multiplying two correlated penalties penalises that population twice for the
same weakness, and 0.6 × 0.6 = 0.36 is an enormous penalty to levy on what may
be a single underlying fact.

**Measure first:** Pearson and Spearman correlation of D and C, conditional by
sector, market-cap bucket, year and distress state. Then choose among:

```
Q = D × C            only if they are genuinely independent
Q = min(D, C)        the binding-constraint reading
Q = w_D·D + w_C·C    a weighted blend
Q = learned          eventually, from evidence
```

The rule is not chosen until the correlation lands.

---

## 4. Confidence never multiplies the headline score by default

```
DO NOT:  ShafferScore × OverallConfidence
```

Display `Score` and `ScoreQuality` **separately**. Whether quality-adjusting the
final score improves prediction is an empirical question the ML layer can test
later — it is not an assumption to build in now.

Note the asymmetry with §2, which is deliberate: shrinking an **input factor**
toward neutral is a statement about that input's reliability. Shrinking the
**final score** is a statement about the whole belief, and would destroy the
distinction between "we are confident this is mediocre" and "we are unsure
whether this is excellent."

---

## 5. Provenance validation: what may promote a fallback

Until dead-company pricing exists, realized returns come from 2,574 listings
that are **all alive in 2026**, where the median survivor underperforms the
total market by −7.45% at 12M. Therefore:

```
Rank agreement                    PRIMARY — may promote a fallback
Survivor-only return behaviour    CORROBORATING DIAGNOSTIC — may not
```

If they conflict, the ranking result wins. A fallback is never promoted on
survivor-only return evidence.

**The first provenance test** (pending, once the staleness fix moves the binding
leaf): EV/EBITDA quality by **debt-rung provenance**. Compare cohorts whose debt
came from the strongest definition against those from lower-confidence rungs. If
ranking is materially unchanged the fallback is acceptable; if the signal
weakens, EV/EBITDA carries a provenance penalty rather than pretending every
ratio is equally clean. Recall that 94% of ladder-v2's debt recovery sits on a
`lower_bound` rung understating debt by a median 14%.

---

## 6. Freeze criteria

No baseline may be called the baseline until:

1. share staleness is fixed and the seasonal artefact **demonstrably disappears**;
2. EPS / P-E coverage is measured;
3. the three share-count coverage figures (~62%, ~52–55%, 25–41%) are reconciled
   into **one published definition**;
4. peer coherence is quantified as a number, not a rung name;
5. the debt-rung effect on EV/EBITDA is measured.

---

## 7. Preregistered prediction — EPS coverage

Recorded **before** the result landed, so it is evidence rather than hindsight:

| Outcome | Reading |
|---|---|
| ~50–65% | P/E really is a broad valuation backbone |
| ~35% | historical valuation itself is sparse; v2 must admit it |
| materially higher | better than expected |
| materially lower | investigate why **before** changing the theory |

Reasoning at the time: EPS is a cover-page-adjacent fact with its own tagging
gaps, and the sign cases remove loss-makers — roughly half the universe at some
dates — so coverage should land well above EV/EBITDA's 34.2% but below
`net_income`'s ~95%.
