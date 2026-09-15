# Strategy Artifact — Shared Structure

Built from `analyst_artifact.schema.json` (and occasionally directly from evidence or the brief). Consumed by the Docs Writer to render the GTM plan sections defined in `PROJECT_BRIEF.md` section 6. Full schema in `strategy_artifact.schema.json`.

Reuses the same citation discipline as the Analyst artifact, via a **`StrategyCitedField`**: `{ value, supporting_ids, basis }`, `basis` again one of `evidence` / `brief_stated` / `inference`. The one change from `CitedField`: `supporting_ids` can reference **either** a raw evidence record (`EV-...`) **or** an Analyst-level finding (`THEME-...`, `SWOT-...`, `ASM-...`, `UNK-...`). This matters because Strategy's intended input, per the guide's flow (`Brief -> Head Planner -> Research -> Analyst -> Strategy -> Docs Writer`), is primarily the Analyst artifact, not raw evidence directly — but a Strategy claim is sometimes more precisely traced to one specific evidence record than to a whole synthesized theme, so both reference types are allowed.

## 1. ICPs (ideal customer profiles)
1–3 `Icp` objects: `icp_id`, `name`, `description` (StrategyCitedField), `current_provider` (StrategyCitedField, value = array of competitor names), optional `demographic_signals`.

**Traceability:** `current_provider` and `description` typically cite Analyst `THEME-` or `SWOT-` IDs (the segment behavior pattern), or `brief_stated` for the base segment definition itself (price/value switchers, per the brief) — never fabricated demographic claims without a `basis`.

## 2. Customer pains and desired outcomes
Array of `PainOutcome`: `pain_statement`, `desired_outcome` (both StrategyCitedField), `related_icp_ids` (≥1), `severity` (StrategyCitedField, `high`/`medium`/`low`).

**Traceability:** pains typically cite Analyst market themes (e.g. `THEME-2`, the promo-cliff theme) or SWOT threats; `severity` should carry the same basis discipline rather than being an unattributed judgment call floating outside the citation system.

## 3. Value proposition
Single object: `headline`, `supporting_points[]`, and two **required** fields — `no_bundle_gap_response` and `low_brand_recognition_response`.

**Traceability + brief compliance:** those last two fields are required, not optional, which schema-enforces the brief's section 7 rule that positioning "must explicitly address the no-mobile-bundle gap and the low-brand-recognition gap — not ignore them." A Strategy artifact that omits either field fails validation before it ever reaches the Docs Writer.

## 4. Positioning and differentiation
`positioning_statement` (classic "for [ICP], Wire3 is the [category] that [X], unlike [competitors] who [Y]" form), `differentiators[]`, `competitive_frame[]` (which named competitors this is positioned against).

**Traceability:** differentiators should cite the Analyst's 7P `market_description` entries or product/feature comparison rows they're contrasting against — a differentiation claim with no Analyst/evidence backing and `basis: "inference"` is allowed but should be rare here specifically, since differentiation is supposed to be grounded in a real observed gap.

## 5. Message pillars
3–5 `MessagePillar` objects: `title`, `supporting_message` (StrategyCitedField), `target_icp_ids`.

**Traceability:** same theme-or-evidence citation pattern as pains; count is bounded (3–5) for the same reason market themes were bounded — to keep density comparable across implementations.

## 6. Recommended channels
Array of `ChannelRecommendation`: `channel_name` (free text), `channel_category` (fixed 5-value enum), `budget_tier` (`low`/`medium`/`high`), `rationale` (StrategyCitedField), `target_icp_ids`.

**Traceability:** `rationale` must cite whatever informed the channel pick (a market theme about how competitors advertise, or an assumption about cost-per-acquisition norms in a regional market) and must explicitly justify the budget fit, directly answering the brief's requirement that channels "fit a regional/local budget, not a national-brand budget."

## 7. Launch phases and activities
Array of `LaunchPhase`: `phase_name`, `sequence_order` (explicit integer), `timeframe`, `activities[]` (StrategyCitedField each), `exit_criteria`.

**Traceability:** activities cite whichever pain/theme/channel they're acting on; `exit_criteria` is plain text (operational, not a factual claim).

## 8. Success metrics
Array of `SuccessMetric`: `name`, `definition` (how it's measured — mirrors the guide's own KPI-documentation pattern from Step 10), `target_value` (StrategyCitedField), `measurement_cadence`.

**Traceability:** `target_value.basis = "evidence"` when benchmarked against a sourced figure (e.g. industry-average CAC), `"inference"` when it's a reasonable internal target with nothing to cite.

## 9. Risks
Array of `Risk`: `statement`, `likelihood`, `impact` (all StrategyCitedField), `mitigation` (StrategyCitedField, usually `inference`).

**Traceability:** a risk like "T-Mobile could undercut our promo price" cites the relevant pricing-matrix/evidence; a risk like "regional brand-building takes longer than one launch cycle" is typically `inference`.

## 10. Follow-up research questions
Array of `FollowUpResearchQuestion`: `question`, `rationale`, optional `related_unknown_ids` (links back to `AnalystArtifact.assumptions_unknowns_conflicts.unknowns`), `priority`.

**Traceability:** this is where the loop closes — an RQ the Analyst flagged as unresolved (`UNK-...`) surfaces here as a named follow-up rather than quietly disappearing, directly supporting the coverage-KPI honesty goal from Step 2.

---

## Ambiguity flags

1. **Does Strategy cite raw evidence or Analyst-synthesized findings?** Left unspecified, one implementation might force everything through Analyst-level IDs (losing precision) and the other might bypass Analyst and re-cite raw evidence everywhere (defeating the point of having an Analyst stage at all). *Fixed:* `SupportingRef` explicitly allows both, via a single multi-prefix pattern.
2. **Value-prop gap coverage is easy to skip silently.** The brief requires positioning to address the no-bundle and low-brand gaps, but nothing stops an implementation from just... not doing that. *Fixed:* `no_bundle_gap_response` and `low_brand_recognition_response` are schema-required fields, not a prose reminder — a Strategy artifact missing either fails validation.
3. **Launch phase ordering via array position is fragile.** JSON array order is easy to lose or scramble across serialization steps (especially in n8n, where items can be split, merged, and reassembled by multiple nodes). *Fixed:* explicit `sequence_order` integer field; consumers must sort on it rather than trust array position.
4. **Channel naming is inherently open-ended,** and forcing it into an enum would be more restrictive than useful (regional GTM channels are genuinely varied). *Partially fixed:* `channel_name` stays free text, but `channel_category` is a fixed 5-value enum kept alongside it, so cross-implementation comparison (e.g. "how many low-cost local_offline channels did each implementation recommend?") is still possible even when exact channel names differ.
5. **Metric targets could be uniformly over- or under-cited.** Without a basis field, one implementation might fabricate a citation for every target number to look rigorous, while the other leaves genuinely benchmarked targets uncited. *Fixed:* same `basis` discipline as everywhere else — `evidence` requires a real `supporting_ids` entry, `inference` doesn't need one.
6. **ICP, pillar, and phase counts need bounds** for the same reason market themes did — otherwise one implementation's 1-ICP, 3-pillar output isn't comparable to the other's 3-ICP, 8-pillar output. *Fixed:* `icps` 1–3, `message_pillars` 3–5.

## Checkpoint self-check (guide, Step 2)

- [x] **Strategy Agent can identify which evidence supports each major recommendation** — every recommendation-bearing field is a `StrategyCitedField`, and the two brief-compliance fields in the value proposition are schema-required rather than optional.
- [x] **Same artifact contract representable in n8n and CrewAI** — plain objects/arrays/enums only; see `n8n_vs_crewai_representation.md` for exactly how each maps.
