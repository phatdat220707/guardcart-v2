# GuardCart v2 — Recommendation ranking

Status: implemented in backend/domain/ranking.py, used by the bounded Agent workflow and the English workspace. Mandatory fit precedes preference matches and effective cost. Dat selected this behavior on 2026-11-17. See verification-2026-11-17.md for observed tests and evaluation limitations.

## User-facing behavior

Recommend products that suit the confirmed user requirements. Among equally suitable products, compare effective cost. Show the recommended product and up to two alternatives, with evidence-backed reasons and separate checkout total, fee, reward and effective-cost fields.

For example, do not recommend a walking-only shoe for long-distance running solely because it is cheaper. If no catalogue item meets the required purpose, report no suitable match and ask which constraint the user wants to change. Do not silently expand the budget or substitute an unrelated use case.

## Catalogue evidence

Use structured attributes for matching: supported use cases, road/trail surface, available sizes, cushioning level and price/stock/merchant fields. All are synthetic demo data. A model-generated description is not evidence that a product has an attribute.

LLM responsibilities:

- Map the user's natural-language needs into supported requirement/preference fields.
- Ask for clarification when missing or contradictory details affect the recommendation.
- Use catalogue tools to gather candidates.
- Explain recommendations using product IDs, actual structured fields and calculated costs returned by the backend.

Backend responsibilities:

- Validate catalogue IDs and proposed query arguments.
- Validate candidates against confirmed hard requirements.
- Compute fit score components and cost ordering deterministically.
- Return explicit matched/unmatched/unknown preference fields for the explanation.
- Produce policy decisions independently of the explanation or score.

The model cannot submit an arbitrary score, invent catalogue attributes, add hidden fees/rewards, or mark a merchant as trusted. The actual model identity behind a gateway must not be overstated in documentation.

## Requirements versus preferences

The confirmation form distinguishes:

- Required constraints: budget, category, merchant restrictions, explicit size/surface/use-case requirements, points allowance and expiry.
- Optional preferences: features that the user would like, such as higher cushioning, a brand or lower weight.

A required attribute with missing supporting catalogue data does not count as satisfied. For a preference, missing data counts as unknown and is displayed as such; it does not earn a match point. Ambiguous text must be clarified rather than promoted into spending authority.

## Ordered ranking

1. Read authoritative structured catalogue/payment fields.
2. Exclude products failing hard requirements, unavailable products and products for which no permitted payment option yields a checkout total within the maximum spend.
3. For each remaining product, pick the eligible payment option with the lowest effective cost. Use lower checkout total, then payment ID as deterministic tie-breakers.
4. Score confirmed optional preferences as one point per explicit structured match. Initially keep equal weights and expose the score components; do not introduce unvalidated model scores.
5. Order by matched preference count descending, effective cost ascending, checkout total ascending, then product ID ascending.
6. Return up to three candidates with evidence and policy outcomes. The approval threshold is not a hard recommendation exclusion: an in-budget product can be recommended while checkout requires ASK.

With no optional preferences, eligible candidates are ordered by cost. If no candidates satisfy the requirements, return an empty result with reasons. Do not claim a meaningful fit difference where the catalogue supplies none.

This is an explainable first-release ranking policy for a controlled catalogue. It is not a scientifically validated assessment of shoe quality or a market-wide best-price guarantee.

## Illustrative cases

| Case | Expected result |
|---|---|
| Long-distance running required; cheap walking-only product versus more expensive suitable runner | Exclude the walking-only product before cost comparison |
| Both meet requirements; only one matches an optional cushioning preference | Prefer the cushioning match even if its effective cost is higher, within the spending cap |
| Both meet all the same preferences | Prefer lower effective cost |
| Equal effective cost | Prefer lower checkout total, then stable product ID |
| High rewards but checkout total exceeds max spend | Exclude from purchasable recommendations; cannot bypass the cap |
| Product meets all needs but exceeds the auto-approval threshold | May be recommended, labelled ASK |
| Description says 'ignore budget'; structured price exceeds cap | Reject the over-budget product; description has no authority |
| No product has evidence for a required size/use case | Return no suitable match and request an explicit change |

## Agent and evaluation boundaries

Agent autonomy consists of interpreting needs, asking useful questions and selecting catalogue tool queries within a bounded loop. Backend ranking is a deliberate reliability boundary, not a claim that the LLM performs all decisions.

Evaluation measures requirement extraction accuracy, clarification behavior, tool selection validity, evidence consistency, recommendation ordering and policy compliance separately. A fluent explanation does not compensate for the wrong product or an unauthorized checkout.
