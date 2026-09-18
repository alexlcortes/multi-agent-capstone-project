# n8n Reliability Layer & Validation Walkthrough

This documents the reliability/observability work added on top of the base
Wire3 GTM pipeline (trigger → Head Planner → Research Agent → Analyst Agent →
Strategy Agent → Docs Writer), per capstone guide Step 5 items 8–11 and Step 8.

## What was added

**5 new nodes** (all Code nodes, wired inline into the existing chain):

| Node | Sits between | Job |
|---|---|---|
| `Init Run Log` | Webhook → Head Planner | Generates `client_run_id`, records `pipeline_start`, passthrough |
| `Log: Head Planner` | Head Planner → AI Agent | Validates the plan is usable (throws if not), logs `agent_end`/`agent_start`, resolves the real `run_id` from the plan |
| `Log: Analyst Agent` | Analyst Agent → Validate Evidence Coverage | Sanity-checks the artifact shape, logs `agent_end` with duration |
| `Log: Strategy Agent` | Strategy Agent → Validate Strategy Grounding | Same, for the Strategy Agent |
| `Finalize Run` | Docs Writer (Placeholder) → *(end)* | Aggregates the whole run, checks budget compliance, decides `success`/`degraded`, writes the final `run_complete` log line, returns the HTTP response |

**5 existing nodes edited** to add logging/validation without changing their
core logic:

- `Build Evidence Records` — logs `agent_end` for the Research Agent (with
  per-tool-call `tool_call` log lines from `intermediateSteps`), logs
  `agent_start` for the Analyst Agent, stamps a timestamp for the next hop.
- `Analyst Agent` (prompt) — tightened from `JSON.stringify($json)` to an
  explicit `{run_id, evidence}` allowlist, so a stray field can never leak
  into the LLM prompt.
- `Validate Evidence Coverage` — now also computes `invalid_url_count`, logs
  a `validation_gate` event, logs `agent_start` for the Strategy Agent.
- `Validate Strategy Grounding` — logs a `validation_gate` event (pass/fail).
- `Validate For Docs Writer` — logs the gate result **before** throwing on
  rejection (so a rejected run is still visible in the record), logs
  `agent_start` for Docs Writer on acceptance.
- `Docs Writer (Placeholder)` — logs a `document_write` event.

**Retry configuration** (Settings → Retry On Fail; n8n only exposes this on
main-graph nodes, not on `ai_languageModel`/`ai_tool` sub-nodes — see
[Retry semantics](#retry-semantics-what-i-verified) below):

| Node | Max Tries | Wait Between Tries | On Error |
|---|---|---|---|
| Head Planner | 3 | 8000 ms | Stop Workflow |
| AI Agent (Research Agent) | 3 | 8000 ms | Stop Workflow |
| Analyst Agent | 3 | 8000 ms | Stop Workflow |
| Strategy Agent | 3 | 8000 ms | Stop Workflow |

The AI Agent's **Max Iterations** was also raised from the n8n default of 10
to 50 — the default silently capped the Research Agent at 10 loop iterations,
which is fewer than a single planned tool call per iteration for any brief
needing more than ~9 tool calls (ours regularly plans 14–18). Left at the
default, this would have failed every run with a brief of realistic size
without ever producing a clear "budget exceeded" signal — the agent just hit
`Max iterations (10) reached` mid-plan.

## Run-record schema

Every event is one line in `n8n/logs/runs.jsonl`:

```json
{"ts": "...", "implementation": "n8n", "event_type": "...", "client_run_id": "...", "run_id": "...", "node": "...", "status": "...", ...}
```

`event_type` is one of: `pipeline_start`, `agent_start`, `agent_end`,
`tool_call`, `validation_gate`, `document_write`, `run_complete`.

`client_run_id` is generated at the webhook before the business `run_id`
exists (Head Planner invents `run_id`); every later event carries both, so a
run is traceable end-to-end even if Head Planner's own plan is malformed.

## Retry semantics — what I verified

n8n's "Retry On Fail" / Max Tries / Wait Between Tries settings are only
available in the Settings tab for **main-graph nodes** — confirmed by
inspecting `OpenAI Chat Model` and `MCP Research Tools` (both `ai_*` sub-nodes
consumed by the AI Agent): their Settings tabs have no retry fields, only
Notes. The four LLM/agent nodes (Head Planner, AI Agent, Analyst Agent,
Strategy Agent) do expose it, since they're regular nodes in the main graph.

Practical effect: an individual failed MCP tool call is **not** mechanically
retried — LangChain's tool-calling loop catches the error and feeds it back
to the model as an observation, and our system prompt tells the agent to
note the failure and continue with the remaining planned calls (this is
exactly what `Build Evidence Records`' `unparseable_observations` array and
per-call `tool_call` log lines are for). What retryOnFail actually protects
against is the **whole Research Agent step** failing outright (e.g. the
LLM call itself hits a rate limit) — that retries just that one step, not
the webhook or Head Planner, so a retry never duplicates work that already
succeeded upstream.

## Validation questions (from the capstone guide), answered against this workflow

**Does the Head Planner receive the complete brief?**
Yes — the Webhook's raw body is passed through `Init Run Log` (pure
passthrough) directly into Head Planner's prompt as
`JSON.stringify($json.body)`, so every field the caller POSTs is visible to
it, unfiltered.

**Can the Research Agent return all evidence needed by the Analyst Agent?**
Yes, verified in the success run: Head Planner planned 14 tool calls across 3
research questions, the Research Agent executed all 14 with 0 failures, and
`Build Evidence Records` produced 178 evidence records covering both research
questions with linked `research_question_id`s.

**Does the Analyst Agent preserve evidence IDs when creating tables and synthesis?**
Yes — `Validate Evidence Coverage` walks the entire Analyst artifact for
`EV-xxxxxxxx`-shaped strings and cross-checks every one against the real
evidence set. In the success run: 100% coverage (`valid_reference_coverage_percent: 100`,
`invalid_unique_ids: []`).

**Does the Strategy Agent distinguish evidence from assumptions?**
Yes, and this was tested adversarially by an actual run: `Validate Strategy
Grounding` classifies every cited field by `basis` (`evidence`/`brief_stated`/
`inference`) and rejects any `evidence`-basis field with no real citation. In
the failed run below, it caught the Strategy Agent citing its own
`PHASE-1` launch-phase ID as a "supporting" evidence reference for a risk
mitigation — not a real evidence/theme/SWOT/assumption ID — and blocked it.

**Does a failed search retry without duplicating the entire run?**
The webhook is never re-triggered and Head Planner is never re-run by any
retry in this workflow — retries are scoped to the single agent step that
failed (see [Retry semantics](#retry-semantics-what-i-verified)). Individual
tool-call failures don't retry mechanically, but they also don't restart
anything: they're caught, logged, and the plan continues.

**Is the run time and cost recorded?**
Time: yes, fully — every `agent_end`/`run_complete` event carries
`duration_ms`, and `Finalize Run` computes total wall-clock time and compares
it against the brief's `budget.max_wall_clock_minutes`, flagging (`issues`)
rather than silently overrunning. Cost: partially — `tool_calls_executed` is
compared against `budget.max_search_calls`, and the brief's
`budget.max_cost_usd` ceiling is carried into every `run_complete` line, but
actual per-run dollar cost isn't computed from token usage in this pass
(n8n's own execution UI shows per-call token counts — visible via each LLM
sub-node's Output/Logs panel — but that isn't currently piped into the code
nodes' `$json`). This is the one guide-requested field left as a known gap.

**Does the Docs Writer reject incomplete or invalid content?**
Yes, demonstrated live: `Validate For Docs Writer` rejected the failed run
below with a specific, itemized reason (unresolved `PHASE-1` citation) before
`Docs Writer (Placeholder)` ever ran, and the placeholder itself independently
refuses anything not marked `status: 'accepted'` by the gate.

## Test run (success)

- `run_id`: `r-ocala-001` / `client_run_id`: `client-mu7kxqi3-kboxtt`
- Brief: 3 research questions (footprint/speed, pricing, switch triggers), 3 competitors
- Result: **success**, 0 issues
- Total duration: 329.3s (5.5 min, well under the 12-minute budget)
- Head Planner: 34.1s, planned 14 tool calls for 3 RQs
- Research Agent: 47.4s, executed 14/14 tool calls, 0 failures, 178 evidence records
- Analyst Agent: 164.2s — evidence coverage 100%, 0 invalid URLs
- Strategy Agent: 83.4s — grounding clean (28 evidence-basis fields, 27 inference, 1 brief_stated, 0 violations)
- Docs Writer: accepted, 2 ICPs, 3 message pillars, 4 channels
- Full HTTP response: `status: "success"`, artifact contains all 11 required StrategyArtifact top-level fields

Raw log lines: `n8n/logs/runs.jsonl`, all lines with
`"client_run_id":"client-mu7kxqi3-kboxtt"`.

## Failed run (validation gate rejection)

This was not manufactured after the fact — it's the run immediately before
the success run above, kept because it's a clean, real demonstration of the
grounding gate working exactly as designed (an LLM citation mistake, caught
before it reached Docs Writer).

- `run_id`: `rOCALA01` / `client_run_id`: `client-mu7k77m2-6ufjtc`
- Head Planner: ok, 16 planned tool calls
- Research Agent: ok, 16/16 tool calls, 205 evidence records
- Analyst Agent: ok, evidence coverage 100%
  - Note: this run's `invalid_url_count: 205` was itself a bug (n8n's Code
    node sandbox doesn't expose the global `URL` class, so `new URL(...)`
    threw on every record and got counted as invalid) — fixed by switching to
    a regex check in `Validate Evidence Coverage` and `Finalize Run` before
    the success run above, which correctly shows `invalid_url_count: 0`.
- Strategy Agent: ok (produced output), but...
- **Validate Strategy Grounding: REJECTED** —
  `unresolved_supporting_ids: [{"path":"risks[1].mitigation","id":"PHASE-1","basis":"inference"}]`.
  The Strategy Agent cited its own `launch_phases` ID (`PHASE-1`) as a
  supporting reference for a risk mitigation, which isn't a valid citation
  type (only real evidence IDs, or the Analyst's theme/SWOT/assumption/unknown
  IDs, are valid supporting references).
- **Validate For Docs Writer: REJECTED** — refused to forward the artifact,
  with the specific validation error included in the log line.
- Docs Writer never ran. The webhook call returned HTTP 500 with
  `{"message":"Error in workflow"}` to the caller.

Raw log lines: `n8n/logs/runs.jsonl`, all lines with
`"client_run_id":"client-mu7k77m2-6ufjtc"`.

## Other issues found and fixed during this pass

1. **AI Agent Max Iterations default (10) too low** — see above. Raised to 50.
2. **`new URL()` unavailable in Code node sandbox** — silently made every
   evidence record look URL-invalid. Replaced with a regex check
   (`/^https?:\/\/([\w-]+\.)+[a-z]{2,}(:\d+)?(\/[^\s]*)?$/i`) in both
   `Validate Evidence Coverage` and `Finalize Run`.
3. **OpenAI org-level TPM rate limits** (500,000 TPM) were hit repeatedly
   during back-to-back manual test runs against the same account/org — not a
   workflow bug, but worth knowing: a long agent trajectory (accumulating
   tool-result context across 15+ ReAct iterations) plus several closely
   spaced test invocations can exceed a shared org ceiling. The Wait Between
   Tries (8s) is enough for OpenAI's own suggested backoff (~3–4s) on a single
   call, but won't reliably clear the window if a whole-step retry re-runs a
   long trajectory that immediately re-accumulates the same token load.

## Exporting the workflow

`n8n/workflows/wire3_gtm_pipeline.json` is the current export (via the
n8n UI's "Export JSON"), taken right after the success run above. Verified
secret-free: `grep` for `sk-`/`apiKey`/`api_key` literals returns nothing —
the only `credentials` entries are `{id, name}` references to the "OpenAI
account" credential object; the real key is injected at process start via
`CREDENTIALS_OVERWRITE_DATA` in `start.sh`, sourced from the gitignored
`.env`, and never touches the exported JSON (see SETUP_DECISIONS.md).
