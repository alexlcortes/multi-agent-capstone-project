# Research Evidence Record — Shared Artifact Contract

This is the contract for the data the Research Agent produces and everything downstream (Analyst, Strategy, Docs Writer) consumes. It must be representable identically in both the n8n and CrewAI implementations — same fields, same meaning, same required/optional split — even though the internal representation differs (n8n: JSON moving between nodes; CrewAI: a structured model/dict passed between tasks).

An evidence set for a run is simply an array of these records: `evidence: EvidenceRecord[]`.

## Field reference

| Field | Type | Required | Allowed values / format | Purpose |
|---|---|---|---|---|
| `evidence_id` | string | yes | `EV-` + 8 hex chars, e.g. `EV-4a1f9c02`. Recommended: hash of `source_url + claim`, not a sequential counter, so identical facts get identical IDs across re-runs. | Join key every downstream artifact cites. |
| `research_question_id` | string | yes | Must match an RQ id from the current brief (`RQ1`..`RQ8`). | Groups evidence by research question; drives the coverage KPI. |
| `claim` | string | yes | Free text, ≤400 chars, one atomic fact. | The table/citation-ready statement. |
| `source_title` | string | yes | Free text. | Human-readable label in the citations appendix. |
| `source_url` | string (URI) | yes | `http(s)://...` | The evidence itself; link-validated before final output. |
| `source_type` | enum | yes | `primary`, `company`, `analyst`, `news`, `community` | Drives the ≥80%-top-tier/primary source-quality KPI and confidence weighting. |
| `publication_date` | string (date) or `null` | yes (nullable) | ISO 8601 date, or explicit `null` | Flags staleness of pricing/promo facts. |
| `retrieval_timestamp` | string (date-time) | yes | ISO 8601 datetime, UTC | Cache TTL and reproducibility/drift checks. |
| `excerpt` | string | yes | Free text, ≤600 chars, verbatim/near-verbatim from the source | Hallucination check — must be checkable against the real page. |
| `validation_status` | enum | yes | `verified`, `unverified`, `broken_link`, `low_confidence`, `conflicting` | Gates whether Analyst/Strategy may treat the claim as fact vs. flagged assumption. |

## Field lifecycle notes (matters for either implementation)

- **`evidence_id` and `retrieval_timestamp`** are set once, by the Research Agent / MCP research tool, at fetch time. Never regenerated later.
- **`validation_status`** starts as `unverified` at creation. A link-validation step (Step 8 safeguard) flips it to `verified` or `broken_link`. The Analyst Agent may later flip a record to `conflicting` if it finds another record for the same `research_question_id` that contradicts it — when that happens, both conflicting records stay in the evidence set (nothing is deleted) and the conflict itself gets surfaced in the Analyst artifact's "assumptions, unknowns, and conflicting evidence" section, referencing both `evidence_id`s.
- **`publication_date` absent ≠ stale.** Treat `null` as "unknown," not as a validation failure — many competitor pricing pages simply don't expose a date. `retrieval_timestamp` is what makes drift detectable regardless.
- Records with `validation_status` other than `verified` may still be used by Strategy, but must be visibly flagged (not silently cited as if equivalent to a verified primary source) — this is what satisfies "flag uncited claims rather than silently accepting them."

## Example record

```json
{
  "evidence_id": "EV-4a1f9c02",
  "research_question_id": "RQ2",
  "claim": "Spectrum's standalone 300 Mbps internet plan in Ocala, FL renews at $89.99/mo after a 12-month promo rate of $49.99/mo.",
  "source_title": "Spectrum Internet Plans - Ocala, FL",
  "source_url": "https://www.spectrum.com/internet/ocala-fl",
  "source_type": "primary",
  "publication_date": null,
  "retrieval_timestamp": "2026-09-15T18:32:04Z",
  "excerpt": "After 12 mos, or if any service is cancelled, the standard monthly rate applies... Internet Ultra $89.99/mo.",
  "validation_status": "verified"
}
```

## What each downstream consumer needs from this contract

- **Analyst Agent**: groups records by `research_question_id` and `source_type` to build the competitor comparison table and pricing matrix; every cell must carry the originating `evidence_id`(s); records with `validation_status != "verified"` go into the assumptions/unknowns section, not the main tables.
- **Strategy Agent**: when making a recommendation (e.g. a pricing/positioning claim), must attach the `evidence_id`(s) it's grounded in; if none exist for a claim, that claim must be labeled as an assumption, not a finding.
- **Docs Writer**: renders `source_title` as clickable link text pointing at `source_url`, keyed by `evidence_id`, in the citations appendix; a post-write check confirms every `evidence_id` referenced in the body actually exists in the evidence set (catches orphaned citations).

## Checkpoint self-check (guide, Step 2)

- [x] **Research Agent can return structured evidence rather than only prose** — the schema is a flat, machine-parseable object; nothing here requires natural-language parsing downstream.
- [x] **Analyst Agent can consume research without repeating the web search** — `claim` + `excerpt` give Analyst enough to build tables and prose without refetching pages; it only needs to re-fetch if it wants to escalate a `low_confidence`/`unverified` record.
- [x] **Strategy Agent can identify which evidence supports each major recommendation** — every record has a standalone `evidence_id` designed to be cited, and `research_question_id` lets Strategy check it's grounding claims in evidence tied to the actual brief.
- [x] **Same artifact contract representable in n8n and CrewAI** — the schema is plain JSON with primitive types and enums only (no framework-specific objects), so it maps directly to n8n items passed between nodes and to a shared model/dict passed between CrewAI tasks.
