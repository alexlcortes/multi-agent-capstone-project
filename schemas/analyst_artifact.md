# Analyst Artifact — Shared Structure

Everything here is built strictly from `research_evidence.schema.json` records (or, for a small set of Wire3-only facts, directly from `PROJECT_BRIEF.md`). Full JSON Schema is in `analyst_artifact.schema.json`. This doc explains the shape of each of the seven Analyst outputs and, per the guide's rule ("every factual statement should be traceable to one or more evidence IDs"), exactly how traceability is enforced for each one.

The core mechanism used everywhere below is the **`CitedField` wrapper**: instead of a bare value, every factual field is `{ value, evidence_ids, basis }`, where `basis` is one of:
- **`evidence`** — grounded in ≥1 records from the evidence set (`evidence_ids` required non-empty).
- **`brief_stated`** — a given input taken directly from `PROJECT_BRIEF.md` (e.g. "Wire3 has no mobile bundle"), not external research.
- **`inference`** — the Analyst's own judgment/synthesis; `evidence_ids` may be empty.

This three-way split exists because not everything in these artifacts is an externally-sourced fact — some of it is a structural given from the brief, some of it is the Analyst reasoning across facts. Collapsing those three into one undifferentiated "value" field is exactly where an ungrounded claim could slip through looking as solid as a cited one.

## 1. Competitor comparison table
**Shape:** exactly 4 rows (Wire3 + Spectrum + AT&T + T-Mobile Home Internet), each with `footprint_confirmed_in_region`, `service_type`, `has_mobile_bundle_available`, `top_advertised_speed_mbps_down/up`, optional `summary_note` — all `CitedField`.

**Purpose:** a coarse, executive-summary-level identity snapshot — "who are we comparing and what kind of service is each." Deliberately *not* where detailed specs live (that's the product/feature table).

**Traceability:** `footprint_confirmed_in_region` must cite RQ1 evidence; `has_mobile_bundle_available` for competitors cites RQ2/RQ5 evidence, while for the Wire3 row it must be `basis: "brief_stated"` — never `"evidence"`, since Wire3's own lack of a mobile bundle is a given fact from the brief, not something researched.

## 2. Pricing matrix
**Shape:** one row per plan tier per competitor (≥1 row per competitor). Columns: `plan_name`, `speed_mbps_down`, `promo_status`, `promo_price_usd_per_month`, `promo_duration_months`, `post_promo_price_usd_per_month`, `equipment_fee_usd_per_month`, `early_termination_fee_usd` — all `CitedField` — plus `blended_12mo_effective_price_usd` as a **`DerivedField`**, not a `CitedField`.

**Purpose:** the detailed price comparison that RQ2/RQ3 exist to fill, including the promo-cliff dynamic central to the brief's customer problem.

**Traceability:** every price/fee figure carries the `evidence_id` of the specific pricing/terms page it came from. `promo_status = "no_promo_found"` is itself a claim (an absence) and must cite the page that was checked — only `"not_researched"` is allowed to have empty `evidence_ids`. The blended effective price is *computed*, not sourced, so it uses `DerivedField` instead: `derived_from_evidence_ids` points at the raw price records used, and `formula` restates the fixed calculation (see Ambiguity Flags — this formula is locked, not left to each implementation).

## 3. Product and feature comparison
**Shape:** exactly 4 rows (same competitor set), fixed core columns (`technology_type`, `symmetric_speeds`, `data_cap_present`, `wifi_router_included`, `professional_install_included`, `self_install_available`, `contract_required`, `contract_length_months`, `customer_support_channels`), plus an open-ended `additional_features` array for anything else discovered.

**Purpose:** the deeper technical/spec comparison that feeds value-proposition and positioning work — distinct from the coarse competitor table above.

**Traceability:** every core column is a `CitedField`; `additional_features` entries are `{name, field: CitedField}` pairs, so even opportunistic findings stay individually traceable rather than being dumped into a free-text note.

## 4. Market themes
**Shape:** 3–6 `MarketTheme` objects, each with `theme_id`, `title`, prose `description`, `related_research_question_ids` (≥1), `supporting_evidence_ids` (≥1), optional `competitors_involved`.

**Purpose:** qualitative synthesis across evidence — e.g. "promo-cliff bill shock is the dominant switching trigger across all three competitors" — the kind of insight that doesn't fit a table row.

**Traceability:** deliberately **theme-level, not sentence-level** — one `supporting_evidence_ids` list backs the whole `description` paragraph, rather than trying to cite every clause individually. This is a coarser traceability grain than the tables above, and that's intentional (see Ambiguity Flags).

## 5. SWOT
**Shape:** four arrays (`strengths`, `weaknesses`, `opportunities`, `threats`), each holding `SwotItem` objects with `statement`, `basis`, `evidence_ids`.

**Purpose:** standard synthesis view, but explicitly allowed to mix competitor-research facts, Wire3 given-facts, and pure inference (e.g. "Opportunity: promo-cliff frustration is a switching trigger we can target with transparent flat pricing" is inference built on top of evidence, not a directly sourced claim).

**Traceability:** the same `basis` three-way split as `CitedField`, applied per item. A weakness like "no mobile bundle" is `basis: "brief_stated"`; a threat like "T-Mobile's aggressive promo pricing" is `basis: "evidence"`; a forward-looking opportunity is usually `basis: "inference"`, ideally still pointing at the evidence that prompted it even though it isn't strictly required to.

## 6. 7P analysis (not 4P — see Ambiguity Flags)
**Shape:** an **object**, not an array, with exactly 7 required keys: `product`, `price`, `place`, `promotion`, `people`, `process`, `physical_evidence`. Each holds a `market_description` (`CitedField`) and optional `wire3_relevant_fact` (`CitedField`).

**Purpose:** a factual description of how the *current competitive market* behaves on each P (e.g. "Promotion: all three incumbents rely on 12-month promo-rate discounts as the primary acquisition lever") — analytical, not prescriptive. Wire3's own positioning/strategy response lives in the Strategy Agent's artifact, not here.

**Traceability:** `market_description` cites the evidence describing competitor behavior on that P (often pulling from RQ2, RQ7, RQ8 evidence for promotion/place, RQ1/RQ3 for product/price). `wire3_relevant_fact` is either `brief_stated` or cites Wire3's own public site — never a strategic recommendation dressed up as a market fact.

## 7. Assumptions, unknowns, and conflicting evidence
**Shape:** `{ assumptions: [...], unknowns: [...], conflicts: [...] }`.
- `Assumption`: `id`, `statement`, `rationale`, `evidence_ids` (may be empty).
- `Unknown`: `id`, `research_question_id`, `description`, `reason_unresolved` — for an RQ that couldn't be resolved with public sources.
- `Conflict`: `id`, `research_question_id`, `evidence_id_a`, `evidence_id_b`, `description`, `resolution_status`.

**Purpose:** the explicit safety valve — where evidence is thin, contradictory, or absent, it's *named here* instead of silently smoothed over in a table or theme.

**Traceability:** `conflicts` is the formal counterpart to `validation_status: "conflicting"` on an evidence record — when the Analyst detects two records for the same `research_question_id` that disagree, both original evidence records stay untouched in the evidence set (nothing gets deleted) and the conflict is recorded here referencing both IDs. `unknowns` is what keeps the Step 10 coverage KPI (≥90% of RQs answered with linked sources) honest — an RQ with no usable evidence shows up here rather than just quietly missing from every table.

---

## Ambiguity flags — where n8n and CrewAI could diverge if not fixed here

1. **Competitor table vs. product/feature table overlap.** The guide lists these as separate artifacts but doesn't define the boundary. *Fixed:* competitor table = coarse identity/footprint/bundle snapshot; product/feature table = detailed specs. Without this split, one implementation could put speed tiers in the competitor table and the other in the feature table, making the two outputs structurally incomparable.

2. **Competitor naming/casing.** "AT&T" vs "ATT" vs "AT and T", or "T-Mobile Home Internet" vs "T-Mobile" — free-text competitor names would silently fragment tables and evidence joins across a run, let alone across implementations. *Fixed:* `CompetitorName` is a closed 4-value enum, exact strings only.

3. **Derived numbers computed independently.** `blended_12mo_effective_price_usd` is a calculation, not a sourced fact. Left unspecified, an n8n Function node and a CrewAI LLM call could each apply slightly different rounding or promo-weighting logic and produce different numbers from identical inputs — breaking the whole point of running the same brief through both. *Fixed:* the formula is stated as a literal string in the schema and must be reproduced exactly: `((promo_price * promo_duration_months) + (post_promo_price * (12 - promo_duration_months))) / 12`, with `promo_duration_months = 0` and `promo_price = post_promo_price` when there's no active promo.

4. **Null vs. "not researched" vs. "confirmed no promo."** A bare `null` is ambiguous — did the Research Agent check and find no promo, or just not look? *Fixed:* `promo_status` is an explicit tri-state enum (`has_promo` / `no_promo_found` / `not_researched`), and `no_promo_found` requires its own evidence citation (the page that was checked), so "we looked and there's none" is distinguishable from "we haven't gotten to this yet."

5. **Open-ended feature discovery.** If the Research/Analyst agents are free to surface whatever features they find interesting, one implementation's product/feature table could end up with 6 columns and the other with 11, defeating comparison. *Fixed:* a fixed core column set is required on every row; anything extra goes into `additional_features`, kept structurally separate from the comparable core.

6. **Theme count and citation granularity.** Nothing in the guide caps how many "market themes" there are or how finely each must be cited — one implementation could produce 2 broad themes, another 10 narrow ones, with wildly different citation density. *Fixed:* 3–6 themes required, each with a mandatory `related_research_question_ids` and one theme-level `supporting_evidence_ids` list (not per-sentence).

7. **SWOT items with no clear evidentiary status.** Some SWOT items are facts about Wire3 (from the brief), some are facts about competitors (from research), some are the Analyst's own synthesis. Left unmarked, one implementation might fabricate a citation to make an inference look sourced, while the other leaves genuinely-sourced items uncited. *Fixed:* every `SwotItem` carries the same `basis` enum as `CitedField`.

8. **"4P or 7P" is explicitly left open by the guide.** Since the brief and research questions must be identical across n8n and CrewAI "for a meaningful comparison," an unfixed choice here would break that comparison at the schema level before any prompting differences even come into play. *Fixed:* locked to **7P** (Product, Price, Place, Promotion, People, Process, Physical Evidence) — services-marketing framing fits an ISP better than the goods-oriented 4P — represented as a **7-key object**, not an array, so there's no possibility of missing, duplicate, or reordered categories.

9. **7P vs. Strategy Agent overlap.** Without a stated boundary, both the Analyst's 7P and the Strategy Agent's "positioning" section could end up making the same prescriptive claims about what Wire3 *should* do, in inconsistent language. *Fixed:* 7P's `market_description` is strictly descriptive of the current market; any "what Wire3 should do about it" belongs only in the Strategy artifact.

10. **ID namespace collisions.** Evidence IDs, theme IDs, SWOT item IDs, assumption/unknown/conflict IDs all coexist in the same document. *Fixed:* distinct required prefixes per type (`EV-`, `THEME-`, `SWOT-S/W/O/T`, `ASM-`, `UNK-`, `CONF-`) enforced via regex, so a citation can never be mistaken for the wrong kind of ID.

## Checkpoint self-check (guide, Step 2)

- [x] **Research Agent can return structured evidence rather than only prose** — unchanged from `research_evidence.schema.json`.
- [x] **Analyst Agent can consume research without repeating the web search** — every table/theme/SWOT/7P field pulls from `claim` + `excerpt` already present on the evidence records; nothing here requires re-fetching a source.
- [x] **Strategy Agent can identify which evidence supports each major recommendation** — every Analyst output element carries its own `evidence_ids` (or explicit `brief_stated`/`inference` basis), so Strategy can point at exactly which Analyst finding, and which underlying evidence, justifies a given recommendation.
- [x] **Same artifact contract representable in n8n and CrewAI** — plain objects/arrays/enums throughout, with every place a framework-specific choice could cause divergence (competitor naming, derived-number formula, null semantics, feature set, theme count, P-count/shape) explicitly locked down above rather than left implicit.
