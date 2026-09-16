# Cross-Run Evidence Store — Design Sketch

**Status: proposal, not yet implemented in either build. Deliberately deferred — see "When to build this" below.** This is not part of the required per-run artifact contract (`research_evidence.md`, `analyst_artifact.md`, `strategy_artifact.md`) — a run's output must be valid without this store existing at all. It's an optional, additive persistence layer that makes the hash-based `evidence_id` scheme (see `research_evidence.md`) actually pay off *across* runs instead of only within one.

## Why this is separate from the per-run evidence array

`research_evidence.md` already defines `evidence: EvidenceRecord[]` — the array a single run produces. That array dies with the run unless something writes it down. The store described here is that "something": a durable, keyed index of every `EvidenceRecord` ever produced, across all runs.

## Structure

A JSON object keyed by `evidence_id`, where each entry nests the original record separately from store-only bookkeeping:

```json
{
  "EV-4a1f9c02": {
    "record": {
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
    },
    "store_meta": {
      "first_seen_run_id": "2026-09-15T18:30:00Z",
      "last_seen_run_id": "2026-09-16T09:12:44Z",
      "seen_count": 2
    }
  }
}
```

`record` is exactly a `research_evidence.schema.json` object, unchanged. Bookkeeping lives in a sibling `store_meta` object rather than flattened alongside the record fields, specifically so the store doesn't have to fight `research_evidence.schema.json`'s `additionalProperties: false` — `record` can be validated against that schema as-is, with zero drift between the per-run contract and the stored form.

## `store_meta` field reference

| Field | Type | Purpose |
|---|---|---|
| `first_seen_run_id` | string (date-time) | The `run_id` (see below) of the run that first produced this exact `evidence_id`. |
| `last_seen_run_id` | string (date-time) | The `run_id` of the most recent run that re-derived this same `evidence_id`. |
| `seen_count` | integer | How many runs have independently produced this record. A rising count on an unchanged fact is itself a weak "this is still true" signal, on top of whatever `retrieval_timestamp` says. |

## `run_id`

Not defined anywhere else in the contract yet — proposed here as the UTC ISO 8601 timestamp of when a pipeline run starts (e.g. `2026-09-16T09:12:00Z`), matching the `retrieval_timestamp` convention already used in `research_evidence.md` rather than inventing a new ID format.

## Merge algorithm (runs on top of, not instead of, the normal per-run flow)

1. The Research Agent produces the run's `evidence: EvidenceRecord[]` exactly as `research_evidence.md` already specifies — nothing about that contract changes.
2. After the run's own artifact is finalized (not before — a run must never block on the store being reachable), a persistence step looks up each record's `evidence_id` in the store:
   - **Not found** → insert it; set `first_seen_run_id = last_seen_run_id = <this run's run_id>`, `seen_count = 1`.
   - **Found** → update `last_seen_run_id`, increment `seen_count`. Since `evidence_id` is a hash of `source_url + claim`, a found match should be byte-identical content — if it isn't, that's a hash collision or a bug, and it's worth logging loudly rather than silently overwriting.

## Important limitation: `evidence_id` is not a "same source" key

Because the ID hashes `source_url + claim` together, a genuinely *changed* fact from the same page (e.g. Spectrum raises its post-promo rate) produces a **different** `evidence_id`, not an updated version of the old one. So this store gives you:
- dedup and a permanent citation index (any `evidence_id` ever cited in any generated doc stays look-up-able after the run's raw output is gone), but
- **not** drift detection by `evidence_id` alone. Detecting "this competitor's price changed since last time we checked" requires grouping stored records by `source_url` (and `research_question_id`) across different `evidence_id`s, then diffing their `claim` text — a separate, `source_url`-keyed view over the same store, not something `evidence_id` gives you for free.

## Lookup and search access patterns

- **Exact `evidence_id` lookup** — free with the structure as designed. Since the store is a JSON object keyed by `evidence_id`, retrieving the full record + `store_meta` for any ID (e.g. one cited in a generated GTM doc's citations appendix) is a direct key lookup, not a search — no indexing work needed. This is the primary use case the store exists for.
- **Everything else is a secondary access pattern, not covered by the keyed structure alone:**
  - By `source_url` — needed for the drift-tracking view described above (grouping different `evidence_id`s that came from the same page over time).
  - By `research_question_id` — needed to answer "what evidence, across all runs, has ever been gathered for RQ2."
  - By `source_type`, `validation_status`, or free-text over `claim`/`excerpt` — needed for anything resembling "find all community-sourced claims" or "find records mentioning early-termination fees."
  - None of these are hard, but they all require iterating the store and building a secondary index (or just loading it into something query-capable like SQLite) — they don't fall out of the `evidence_id`-keyed JSON shape for free the way exact-ID lookup does. Worth deciding, when this gets built, whether a flat JSON file is still the right storage choice or whether these secondary patterns tip it toward SQLite/a real DB instead.

## Where it would live per platform

- **n8n:** an n8n **Data Table** (or a Read/Write Binary File node against a JSON file) keyed by `evidence_id`, with a **Code** node doing the found/not-found merge logic before writing back — same "schema enforcement is something you wire, not something free" pattern called out in `n8n_vs_crewai_representation.md`.
- **CrewAI:** a plain JSON file loaded at `Flow` start and written at `Flow` end; a keyed `dict[str, StoreEntry]` makes lookups trivial. No framework feature needed — ordinary Python I/O around the existing `EvidenceRecord` Pydantic model.

## When to build this

Not now. This is a stretch item, not part of the required per-run contract, and the Research Agent that would populate it doesn't exist yet in either implementation. Building it before then means designing merge/staleness logic against imagined data, and it competes for time against the brief's "≤2 focused work sessions per implementation" budget (`PROJECT_BRIEF.md` §7) on a feature that isn't scored.

Come back to this after at least one implementation has a working end-to-end run through the Step 8 link-validation safeguard — ideally after Step 11's n8n-vs-CrewAI comparison is done, so the store is built against whichever platform's real integration point (a specific n8n node, or a specific point in a CrewAI `Flow`) rather than a guess at one.

## Open questions before this becomes real

- [ ] Does this run inside the 12-minute/$2.50 per-run budget (`PROJECT_BRIEF.md` §7), or does it happen as an out-of-band step after budget-relevant work is done?
- [ ] Staleness policy: how old can a stored `verified` record be before a new run should re-fetch rather than reuse it?
- [ ] Does `low_confidence`/`conflicting` content get stored at all, or only `verified`/`broken_link`?
