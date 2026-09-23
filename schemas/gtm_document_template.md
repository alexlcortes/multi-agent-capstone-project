# GTM Document Template — Shared Structure

The layout contract for the Google Doc both Docs Writers produce (`crewai/wire3_gtm/docs_content.py`, `n8n/docs_writer/build_docs_content.js`). The artifact schemas (`research_evidence.md`, `analyst_artifact.md`, `strategy_artifact.md`) define **what** is known; this file defines **where each piece goes on the page and how it looks**, so the two implementations produce documents that can be compared section by section. The field-by-field version of this layout — which artifact field fills which section, column or slot — is `docs_field_map.json`; `crewai/tests/test_docs_field_map.py` fails if any schema field is missing from it.

The Docs Writer stays a renderer, not an agent: every sentence, cell and number below is filled deterministically from validated artifacts. No LLM call writes document text, and nothing appears that is not in an artifact. If a section has no data, it shows its empty state (below). It never shows invented filler.

## Document outline

| # | Section | Built from | Layout |
|---|---|---|---|
| — | Header block | run metadata, brief | title, meta line, disclaimer, reading key |
| 1 | Executive summary | Strategy + Analyst | 6 fixed labelled statements |
| 2 | Research scope | ResearchPlan, analyst report | short paragraph + **RQ coverage table** |
| 3 | Evidence table | EvidenceRecord[] (cited only) | **table**, grouped by RQ |
| 4 | Competitor comparison | `competitor_comparison_table`, `product_feature_comparison` | **4.1 snapshot table**, **4.2 feature table** |
| 5 | Pricing matrix | `pricing_matrix` | **table**, grouped by provider |
| 6 | Market analysis | `market_themes`, `swot`, `seven_p_analysis` | themes as H3 + prose; **SWOT 2×2 table**; **7P table** |
| 7 | GTM recommendations | Strategy artifact | 7.1–7.8 subsections, several tables |
| 8 | Assumptions, risks and open questions | `assumptions_unknowns_conflicts`, `risks`, `follow_up_research_questions` | 5 **tables** |
| A | Appendix A: Analyst findings | THEME/SWOT/ASM/UNK ids cited anywhere | bullet list |
| B | Appendix B: Citations | EvidenceRecords whose id appears anywhere | bullet list, titles hyperlinked |

The framework is **7P, not 4P**. `analyst_artifact.md` flag 8 already fixes this choice, so the template does not reopen it.

### Coverage of the brief's 13 required sections (`PROJECT_BRIEF.md` §6)

| Brief § | Where it lands |
|---|---|
| 1 Executive summary | 1 |
| 2 Research scope & evidence table | 2 + 3 |
| 3 Competitor comparison | 4.1 + 4.2 (speed and bundles in 4.1; contract terms in 4.2); price and fees per plan are in 5 |
| 4 Pricing matrix | 5 |
| 5 ICPs | 7.1 |
| 6 Pains and outcomes | 7.2 |
| 7 Value proposition and positioning (both gaps) | 7.3 + 7.4 |
| 8 Message pillars | 7.5 |
| 9 Channels (regional budget) | 7.6 |
| 10 Launch phases | 7.7 |
| 11 Success metrics | 7.8 |
| 12 Risks, assumptions, follow-ups | 8 |
| 13 Citations / evidence appendix | A + B |

---

## Global conventions

**Citations inline.** Every cited value is followed by its ids in brackets: `$49.99 [EV-4a1f9c02]`. Multiple ids are comma-separated in the same bracket. A value with no evidence uses a basis marker instead:

| `basis` | Marker | Meaning |
|---|---|---|
| `evidence` | `[EV-…]` or `[THEME-…]` etc. | sourced; resolves in Appendix A or B |
| `brief_stated` | `[brief]` | a given fact from `PROJECT_BRIEF.md` |
| `inference` | `[inference]` | judgment with no direct source |
| derived | `[EV-…] (computed)` | `DerivedField`; the formula is printed once, under the table |

**Citations in table cells.** Long id lists make cells hard to scan, so a cell holds the value on line 1 and the bracketed ids on line 2 in 9pt grey (`#5f6368`). The text is the same as the inline form (the verifier's `EV-` regex still finds every id). Only the styling differs.

**Missing values.** Never a blank cell. Use one of the following:
- `n/a`: the field does not apply (e.g. promo months when there is no promo).
- `not found`: `promo_status = no_promo_found`, which is itself cited.
- `not researched`: `promo_status = not_researched`, or a null `CitedField`.
- `unverified`: `footprint_confirmed_in_region` is false or unknown. The brief requires this to be stated, never assumed.

**Formatting.** Booleans render as `Yes`/`No`. Prices render as `$49.99`. Speeds render as `300/300 Mbps` (down/up) in Table 4.1 and as download only in Table 5, which has no upload field. Enums are shown in sentence case (`local_offline` → `Local offline`). Ids stay verbatim.

**ID columns.** Every table built from an array with its own id (7.2, 7.5–7.8, 8.1–8.5) has `ID` as its first column, so Appendix A and cross-references resolve by eye.

**Numbering.** H2 = `N. Section`, H3 = `N.M Subsection` (sections 4, 6, 7, 8 only). Tables get a bold caption line above them: `Table 5 — Pricing matrix`. The post-write verifier checks headings, so the numbering must be stable: an empty section keeps its number and shows its empty state.

**Empty state.** One italic line: *None recorded in this run.* If an `UNK-` id covers the gap, add it: *Not resolved from public sources — see UNK-3.*

**Google Docs rendering.** Tables use native `insertTable`, not bullet lists (see "Implementation notes" at the end).

---

## Header block

```
Wire3 Go-To-Market Plan                                         [TITLE]
Run: <run_id> | Brief: <brief_id> | Generated: <YYYY-MM-DD HH:MM> UTC | Sources cited: <n>
<disclaimer paragraph>
<reading key paragraph>
```

- **Disclaimer** (fixed text, required by brief §9): "Academic strategy exercise for the capstone: recommendations are illustrative and grounded in public evidence, not Wire3's actual go-to-market plan. Wire3 facts come only from Wire3's public website or the project brief."
- **Reading key** (fixed text): what `[EV-…]`, `[THEME-…]/[SWOT-…]/[ASM-…]/[UNK-…]`, `[brief]`, `[inference]` and `(computed)` mean, and which appendix resolves each.
- Doc title (Drive name): `Wire3 GTM Plan - <run_id> - <when> UTC` (unchanged).

---

## 1. Executive summary

Six labelled paragraphs, always in this order and always all present. Each one is a sentence built from a template around artifact values, with the source's citation attached. It is one screen long and readable without the rest of the document.

| Label | Filled from | Template |
|---|---|---|
| **Target** | `plan.segment`, `plan.region`, `icps[*].name` | "Price/value switchers in Ocala / Marion County, FL, in {n} profiles: {icp names}." |
| **Why they switch** | highest-`severity` entry in `pains_and_outcomes` (ties: first) | "{pain_statement} [ids]" |
| **Positioning** | `positioning.positioning_statement` | verbatim + ids |
| **How Wire3 closes its gaps** | `value_proposition.no_bundle_gap_response`, `.low_brand_recognition_response` | two sentences, each + ids |
| **First moves** | lowest `sequence_order` phase; channels with `budget_tier = low` | "{phase_name} ({timeframe}) via {low-budget channel names}." |
| **Confidence** | analyst report + `unknowns`, `risks` | "{cited_sources} sources, {top_tier_share}% primary/analyst (target 80%). {n} research questions unresolved ({UNK ids}); highest-impact risk: {statement}." |

The Confidence line is required. If the plan rests on thin evidence, the reader should learn that on page 1, not in section 8.

---

## 2. Research scope

**Paragraph:** "{planned calls} planned research calls returned {n} evidence records, retrieved {first date} to {last date}. Sources were limited to public pages; Wire3 facts come only from wire3.com."

**Table 2 — Research question coverage** (one row per RQ in the plan, in RQ order):

| RQ | Question | Sources cited | Source mix | Status |
|---|---|---|---|---|
| RQ1 | What is each competitor's actual serviceable footprint… | 4 | primary 3, aggregator 1 | Answered |
| RQ5 | How much does a bundled mobile plan influence… | 1 | analyst 1 | Thin (1 source) |
| RQ6 | … | 0 | — | Unresolved — UNK-2 |

- `Sources cited` = distinct evidence ids for that RQ that are cited anywhere in the document.
- `Status`: `Answered` (≥2 cited sources), `Thin (1 source)`, or `Unresolved — UNK-n` (0 cited sources; it must have a matching unknown or preflight fails).

**Evidence-quality notes** (bullets, shown only when non-zero, same set as today): pricing repairs, unsupported numbers, dropped invented ids, theme RQ-link repairs, accepted coverage gaps, salvaged draft.

---

## 3. Evidence table

Shows what the research actually established, one claim per row. It is separate from Appendix B, which only records where each claim came from.

**Table 3 — Evidence** (only records cited somewhere in the document; grouped by RQ, then by `evidence_id`):

| ID | RQ | Claim | Source type | Status | Retrieved |
|---|---|---|---|---|---|
| EV-4a1f9c02 | RQ2 | Spectrum's standalone 300 Mbps plan in Ocala renews at $89.99/mo after a 12-month $49.99 promo. | primary | verified | 2026-09-15 |

- `Claim` is the record's `claim` verbatim. It is ≤400 chars and "table-cell-ready" by schema, so there is no truncation.
- `Status` is `validation_status`. Rows with `conflicting` also show the `CONF-` id: `conflicting (CONF-1)`.
- The excerpt is not shown here (too long). Appendix B links the source instead.
- Uncited records are left out and counted in a closing line: "{k} further records were collected but not cited."

---

## 4. Competitor comparison

### 4.1 Snapshot

**Table 4.1 — Competitor snapshot** (exactly 4 rows, fixed order: Wire3, Spectrum, AT&T, T-Mobile Home Internet):

| Provider | Service type | Footprint in Ocala | Mobile bundle | Top speed (down/up) | Note |
|---|---|---|---|---|---|
| Wire3 | Fiber [brief] | … | No [brief] | … | … |

- `Footprint in Ocala` shows `unverified` unless `footprint_confirmed_in_region` is true with evidence. This is the brief's "say so explicitly rather than assume overlap" rule, made visible.

### 4.2 Features and contract terms

**Table 4.2 — Feature comparison.** This table is transposed: rows are attributes, columns are the 4 providers. With a fixed provider set, it scans far better than 4 rows × 10 columns.

| Attribute | Wire3 | Spectrum | AT&T | T-Mobile HI |
|---|---|---|---|---|
| Technology | | | | |
| Symmetric speeds | | | | |
| Data cap | | | | |
| Wi-Fi router included | | | | |
| Professional install | | | | |
| Self-install available | | | | |
| Contract required | | | | |
| Contract length (months) | | | | |
| Support channels | | | | |

`additional_features` go below the table as bullets, grouped by provider: `Spectrum — {name}: {value} [ids]`. They stay out of the table so every run's table has the same 9 comparable rows (`analyst_artifact.md` flag 5).

---

## 5. Pricing matrix

**Table 5 — Pricing matrix** (grouped by provider in the fixed order, then by speed ascending):

| Provider | Plan | Speed (down) | Promo price | Promo months | Post-promo | Equipment /mo | ETF | 12-mo effective |
|---|---|---|---|---|---|---|---|---|

- The provider name appears on the first row of its group only (merged-cell look without real merges, which the Docs API handles poorly).
- `12-mo effective` is the `DerivedField`. Under the table, one line prints the locked formula from `analyst_artifact.md` flag 3 once, instead of repeating it in every cell.
- **The promo cliff:** when post-promo > promo price, the Post-promo cell is bold. This makes the brief's central customer problem visible in the table itself.
- **Standalone vs. bundled:** the schema holds standalone internet tiers only. A fixed line under the table says so: "Bundled (internet + TV + mobile) pricing is not tabulated; bundle availability is in Table 4.1." A missing column should be stated, not left for the reader to discover.

---

## 6. Market analysis

### 6.1 Market themes
One H3 per theme: `THEME-n: {title}`. Then the `description` paragraph, then one grey line: `Research questions: RQ2, RQ6 · Competitors: Spectrum, AT&T · Evidence: [EV-…, EV-…]`.

### 6.2 SWOT

**Table 6.2 — SWOT** (2×2 table: Strengths | Weaknesses over Opportunities | Threats). Each cell contains its items as lines of the form `SWOT-S1 {statement} [ids or basis marker]`, with the id in bold. Internal factors (S, W) sit on the top row and external factors (O, T) on the bottom, in the conventional layout.

### 6.3 7P market analysis

**Table 6.3 — 7P** (exactly 7 rows in fixed order: Product, Price, Place, Promotion, People, Process, Physical evidence):

| P | How the market behaves | Wire3-relevant fact |
|---|---|---|

This section is descriptive only (`analyst_artifact.md` flag 9). A caption line says so: "What Wire3 should do about each P is in section 7."

---

## 7. GTM recommendations

The prescriptive half of the document. Every row carries the Strategy `supporting_ids`, which mostly point to Analyst findings (Appendix A), not raw evidence.

### 7.1 Ideal customer profiles
One H3 per ICP (`ICP-n: {name}`) with labelled bullets: Description, Current provider, Demographic signals.

### 7.2 Customer pains and desired outcomes

**Table 7.2** (sorted by severity, high → low):

| Pain | Desired outcome | Severity | ICPs |
|---|---|---|---|

### 7.3 Value proposition
- **Headline** as a single emphasized paragraph + ids.
- **Supporting points** as bullets.
- **Two required callouts** in 1×1 shaded tables (light grey fill), always present, labelled exactly:
  - **No mobile bundle — our response:** `no_bundle_gap_response`
  - **Low brand recognition — our response:** `low_brand_recognition_response`

  Brief §6.7 says these must not be ignored. A dedicated box makes it impossible for them to get lost in a bullet list.

### 7.4 Positioning and differentiation
The positioning statement as a paragraph. Differentiators as bullets. The competitive frame as one line: "Positioned against: Spectrum, AT&T, T-Mobile Home Internet."

### 7.5 Message pillars

**Table 7.5:** `Pillar | Message | Target ICPs`

### 7.6 Recommended channels

**Table 7.6** (sorted by budget tier, low → high):

| Channel | Category | Budget tier | Target ICPs | Rationale |
|---|---|---|---|---|

Closing line computed from the rows: "{n_low} of {n} channels are low-budget; {n_local} are local/offline or partnership." This answers brief §6.9 (regional budget) with a count rather than an assertion.

### 7.7 Launch phases and activities

**Table 7.7** (sorted by `sequence_order`, never array position — `strategy_artifact.md` flag 3):

| # | Phase | Timeframe | Activities | Exit criteria |
|---|---|---|---|---|

The Activities cell holds one line per activity, each followed by its ids.

### 7.8 Success metrics

**Table 7.8:** `Metric | Definition | Target | Cadence`. The Target cell shows `[inference]` when it is not benchmarked, so a made-up target cannot pass as a sourced one.

---

## 8. Assumptions, risks and open questions

All plan-level uncertainty in one place. It lists the same ids the executive summary's Confidence line counts.

| Table | Columns | Order |
|---|---|---|
| 8.1 Assumptions | ID · Assumption · Rationale · Evidence | id |
| 8.2 Unknowns | ID · RQ · What is unknown · Why unresolved | RQ |
| 8.3 Conflicting evidence | ID · RQ · Source A · Source B · Description · Resolution | id |
| 8.4 Risks | Risk · Likelihood · Impact · Mitigation | impact high → low, then likelihood |
| 8.5 Follow-up research | Question · Priority · Rationale · Closes unknown | priority high → low |

Empty tables are replaced by their empty-state line. A table with zero data rows is never rendered.

---

## Appendix A: Analyst findings referenced

Unchanged from the current writers. One bullet per THEME/SWOT/ASM/UNK id cited anywhere, sorted naturally: `[THEME-2] {title} — [EV-…] [EV-…]`. For an ASM/UNK with no evidence the line reads "no direct source evidence (assumption or open unknown)".

## Appendix B: Citations

Unchanged in content, extended in format. One bullet per EV id found anywhere in the document, natural sort:

`[EV-4a1f9c02] Spectrum Internet Plans - Ocala, FL (primary, retrieved 2026-09-15)`

- The source title is hyperlinked to `source_url`. A non-http URL is shown as plain text, never as a link.
- `publication_date` follows the source type when known. The retrieval date is always shown, because prices change.
- Build rule: an EV id in the body that is missing from the evidence set aborts the write (orphan check, as today).

---

## Worked example (Markdown rendering, abridged)

```markdown
# Wire3 Go-To-Market Plan
Run: 2026-09-21T14:02:00Z | Brief: wire3-ocala-switchers | Generated: 2026-09-21 14:10 UTC | Sources cited: 27

## 1. Executive summary
**Target:** Price/value switchers in Ocala / Marion County, FL, in 2 profiles: Promo-cliff refugees; Fixed-wireless frustrated.
**Why they switch:** Bills jump 40–80% when 12-month promos expire [THEME-2, EV-4a1f9c02]
...
**Confidence:** 27 sources, 74% primary/analyst (target 80%). 1 research question unresolved (UNK-1); highest-impact risk: Spectrum matches promo pricing in Wire3 neighborhoods [EV-9c01aa3e].

## 5. Pricing matrix
**Table 5 — Pricing matrix**
| Provider | Plan | Speed (down) | Promo price | Promo months | Post-promo | Equip. | ETF | 12-mo eff. |
|---|---|---|---|---|---|---|---|---|
| Spectrum | Internet | 300 | $49.99 [EV-4a1f9c02] | 12 [EV-4a1f9c02] | **$89.99** [EV-4a1f9c02] | $0 [EV-…] | n/a | $49.99 (computed) |
12-mo effective = ((promo × promo months) + (post-promo × (12 − promo months))) / 12
```

(Illustrative values only. Real values come from the run's artifacts.)

---

## Implementation notes for both writers

1. **Block model gains a `TABLE` block**: `{style: "TABLE", caption, header: [str], rows: [[Cell]]}`, where `Cell = {text, ids_text, bold}`. `render_markdown` emits a pipe table. The `EV_RE` scan for Appendix B and the orphan check must walk cell text as well as paragraph text.
2. **Docs API tables:** the current single `insertText` becomes a sequence of segments. The paragraph text before a table goes in one `insertText`, then `insertTable{rows, columns, location}`, and so on. Cell start indices only exist after insertion. Two workable options:
   - (a) Compute them. The layout of an empty table is fixed by its shape, so each cell's start index can be computed from the table's insertion index, row, column and column count. The exact offsets are **not yet verified here** and must be confirmed against a real `documents.get` before use. Fill cells **last-to-first** so earlier indices never shift. Everything stays in one atomic `batchUpdate`, which keeps the current "no partial documents" property.
   - (b) Do two passes: insert the skeleton, `documents.get`, then fill. This is simpler to reason about but loses atomicity, and a failed second pass leaves a half-filled document.

   **Recommend (a)** with a unit test that checks the computed indices against a real `documents.get` of one small table, captured once as a fixture.
3. **Verifier (`verify_document.js` / the CrewAI post-write check)** additionally asserts: table count, each table's header row, and a row count for the fixed-shape tables (4.1 = 4, 4.2 = 9, 6.3 = 7, 6.2 = 2×2).
4. **n8n parity.** The n8n writer currently renders only the Strategy artifact (sections 7–8 here). For the comparison to be like-for-like, it should render sections 1–6 from the Analyst artifact too. If not, the section headings should still appear with the line *Not rendered by this implementation.* so the difference is visible, not silent.
5. **Styles:** captions are bold 10pt. Table header rows are bold with light grey fill. The id line in each cell is 9pt `#5f6368`. Body text uses Docs defaults. No other custom styling, to keep the request list small and the verifier simple.
