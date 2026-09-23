# Human-review rubric for GTM plans

Six criteria, each scored **1–5** against the anchors below. Score the document a product or
marketing lead would receive, not the pipeline that made it. Write one line of evidence per score
(a quote, a section name or a count). A score without evidence is not used.

Automated checks (`crewai/wire3_gtm/golden.py`) already cover what code can decide: sections present,
ids resolvable, links live, length within bounds. This rubric is for what code cannot decide: whether
the plan is clear, credible, specific and usable.

## How to review

1. **Blind.** Review documents labelled "Doc 1", "Doc 2", ... without knowing which implementation or
   variant made them (`python -m wire3_gtm ab packets` produces blinded copies). Unblind after scoring.
2. **Read in full first, then score.** Scores are for the whole document.
3. **Score each criterion on its own terms.** A missing pricing matrix lowers usefulness and evidence
   quality. It does not lower clarity if what is there is clear.
4. **Use the anchors, not a gut ranking.** A 3 is "adequate, with real gaps", not "average".
5. **Two reviewers where possible.** If their scores differ by 2+ on a criterion, discuss and record
   the agreed score and the reason.
6. Record scores in `eval/reviews/<date>-<reviewer>.json` (see `eval/reviews/TEMPLATE.json`).

## Criteria and anchors

### 1. Clarity
Can a reader who was not in the project understand the recommendation and why, quickly?

| Score | Anchor |
|---|---|
| 5 | Executive summary states the recommendation, target, offer and top 3 actions with key numbers. Sections flow; every value has units; no internal jargon or raw field names. |
| 4 | Recommendation is clear from the summary; minor friction (some raw labels, some long lists). |
| 3 | Recommendation can be found, but the reader has to assemble it from several sections; units or context missing in places. |
| 2 | No real summary, or the document reads as a data dump; key numbers lack units or context. |
| 1 | Reader cannot tell what is being recommended. |

### 2. Feasibility
Could a small regional team execute this with the brief's constraints (budget, no mobile bundle, low brand recognition)?

| Score | Anchor |
|---|---|
| 5 | Phases are sequenced, owned and time-boxed; each activity is scoped; spend is sized against the budget; targets are justified; dependencies (e.g. unknown prices) are resolved before they are needed. |
| 4 | Sequenced and scoped with realistic targets; budget sizing or ownership thin. |
| 3 | Plausible activities and phases, but targets are illustrative, spend is unsized, or some activities are beyond a small team without saying how. |
| 2 | Activities are generic or depend on things the plan has not established (partners, prices, coverage). |
| 1 | Not executable as written. |

### 3. Differentiation
Does the positioning give Wire3 a reason to win that competitors could not equally claim?

| Score | Anchor |
|---|---|
| 5 | Positioning rests on specific, verified Wire3 facts versus named competitor facts; directly answers the no-bundle and low-recognition constraints; hard for an incumbent to copy. |
| 4 | Specific and evidence-backed, with one pillar that is generic or rests on an unresolved unknown. |
| 3 | Partly specific; several pillars are what any regional ISP would say. |
| 2 | Generic ("local, reliable, transparent") with little Wire3-specific support. |
| 1 | No differentiation, or it contradicts the evidence. |

### 4. Evidence quality
Are the sources behind the claims relevant, authoritative, current and about the right market?

| Score | Anchor |
|---|---|
| 5 | Key claims rest on primary sources (company pages, filings, regulators) for the right market and date; competitor prices come from the competitor; ≥80% top-tier sources (the brief's target); no mismatched citations. |
| 4 | Mostly strong; a few secondary sources for non-critical claims. |
| 3 | Adequate volume, but a meaningful share is aggregator/community content, some citations do not support their claim, or some are for another market. |
| 2 | Thin, largely secondary, or missing evidence for the company itself or for pricing. |
| 1 | Claims are unsupported or supported by irrelevant sources. |

### 5. Citation completeness
Can a reader trace each claim to its source and tell sourced facts from judgment?

| Score | Anchor |
|---|---|
| 5 | Every factual claim is cited; every source is listed with a working link; inference and brief-stated facts are marked; unknowns and conflicts are disclosed. |
| 4 | Complete and traceable; a few labels imprecise. |
| 3 | Most claims traceable, but some numbers or recommendations are cited to findings that do not contain them, or labels are wrong. |
| 2 | Significant claims uncited or untraceable. |
| 1 | Citations largely absent. |

### 6. Usefulness to a product/marketing team
Could the team make decisions on Monday from this document?

| Score | Anchor |
|---|---|
| 5 | Answers the business question: who to target, what to offer at what price versus which competitor price, which channels at what spend, and how success is measured, with the open questions that block launch. |
| 4 | Answers most of it; one decision (often price or spend) still needs work. |
| 3 | Useful direction and competitor context, but two or more core decisions cannot be made from it. |
| 2 | Mostly restates the brief or generic practice; little the team could not have written. |
| 1 | Not usable. |

## Reporting

Report each criterion separately, then the total out of 30. Never report the total alone: a 20 made
of 5s and 1s is a different document from a 20 made of 3s and 4s.
