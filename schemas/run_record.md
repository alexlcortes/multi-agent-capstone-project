# Run Record — Shared Structure

One `run_record` line per pipeline run, appended to `<implementation>/logs/runs.jsonl` after the run's other events. It is the comparable summary. The other events (`pipeline_start`, `agent_start/end`, `tool_call`, `validation_gate`, `document_write`, `run_complete`, `usage_summary`) stay as the detailed trace, and the record is derived only from them. Full schema: `run_record.schema.json`.

| Implementation | Built by | When |
|---|---|---|
| CrewAI | `crewai/wire3_gtm/run_record.py` (`RunMonitor.run_record`) | End of every run, after `usage_summary`; also saved as `runs/<id>/06_run_record.json` |
| n8n | `n8n/scripts/run_record.js` | By `scripts/run_usage.js` right after it writes `usage_summary` (run it after each execution, as before) |
| Either, for older runs | `python -m wire3_gtm run-records` / `node scripts/run_record.js` | Adds a record to every finished run that lacks one; running it twice adds nothing |

`crewai/tests/test_run_record.py` checks both builders (n8n through `node`) and every logged record against the schema. It also checks that both languages compute the same `brief_id`.

## Fields

| Guide asks for | Record field | CrewAI | n8n |
|---|---|---|---|
| Run ID | `run_id` | client run id (run dir name) | client run id |
| Brief ID | `brief_id` | sha256 of `runs/<id>/00_brief.json` | sha256 of the Webhook body in the stored execution |
| Implementation | `implementation` | `crewai` | `n8n` |
| Agent / tool name | `agents[].agent`, `tools.by_tool` | ✓ | ✓ (tool names without the `MCP_Research_Tools_` node prefix) |
| Timestamps | `started_at`, `ended_at`, `duration_ms`, per agent | ✓ | ✓ |
| Status / errors | `status`, `failed_step`, `errors[]`, `issues[]` | ✓ | ✓ (`failed_step` always null: n8n's run_complete has none) |
| Retry count | `retries.{total,tool_call,guardrail,provider}`, `agents[].retries` | counted | only `provider` (HTTP 429 lines in the server log), `basis: "approximate"` |
| Model / provider | `model`, `provider`, `search_provider` | ✓ | `search_provider` not logged |
| Token usage | `tokens`, `agents[].*_tokens` | provider-reported, incl. reasoning and cached | character estimates; no reasoning or cached tokens |
| Estimated cost | `cost.estimated_llm_usd`, `is_lower_bound`, `excludes` | exact for LLM | lower bound (excludes hidden reasoning tokens) |
| Questions answered | `research.questions_answered/_total` | ✓ | ✓ |
| Evidence / source counts | `evidence.records`, `evidence.coverage_percent`, `document.sources_cited` | ✓ | ✓ |
| Broken-link count | `links.broken` (+ `checked`, `invalid`, `blocked`, `unverified`) | HTTP-checked | **not measured**: n8n checks URL format only (`links.invalid`) |
| Doc write status | `document.write_status`, `verified`, `url`, `sections` | ✓ | ✓ |
| Budget | `budget.*`, `within_latency/cost/search_calls` | ✓ | ✓ |
| Cache hits | `tools.cache_hits` | ✓ | not recorded yet (null) |
| Uncited claims | `claims.{evidence,brief_stated,inference,uncited_share}` | ✓ (Strategy) | ✓ (Strategy) |

## Rules

- **Unmeasurable is `null`, never `0`**, and each such path is listed in `not_measured` with the reason. A 0 means "measured, none".
- **`brief_id` is a content hash**, not the id the Head Planner writes. The planner makes up its own `run_id` (kept as `planner_run_id`), and the artifacts copy it into their `brief_id` field, so that field can't be compared across runs. The hash is `'brief-' + sha256(canonical JSON)[:12]`, with keys sorted, no whitespace and UTF-8; Python and JS must agree, and a test checks it.
- **`document.write_status`** is one of `verified` (the read-back check passed), `local_only` (Markdown only, no Google), `placeholder` (an early n8n run before OAuth), `failed` or `not_run`.
- **A run that never wrote `run_complete` gets no record.** Both implementations write `run_complete` however a run ends.

## Known comparability gaps

1. **Resolved 2026-09-23: both implementations now run the same brief.** Earlier n8n runs were given a
   different brief (`brief-accbe7aac579`, typed into the editor's test webhook) from CrewAI's
   (`brief-d516e1daddfb`). Both now read the repo-root `brief.json` (n8n through `n8n/scripts/run_pipeline.js`),
   and every n8n run since execution 32 carries `brief-d516e1daddfb`. Runs of the earlier brief must not be
   compared with current ones. The n8n workflow also changed substantially on 2026-09-23 (planner rules,
   per-company research, full document layout): see `eval/COMPARISON_LOG.md` for which runs compare with which.
2. **Retries, broken links, reasoning tokens and search provider** are measured only by CrewAI (see `not_measured` in each n8n record).
3. **Cost:** n8n's figure is a lower bound from estimated tokens, and CrewAI's is from provider-reported usage. Neither includes search-provider fees.
