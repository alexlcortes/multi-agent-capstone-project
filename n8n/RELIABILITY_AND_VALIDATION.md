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
| `Finalize Run` | Verify Document & Log → *(end)* | Aggregates the whole run, checks budget compliance, decides `success`/`degraded`, writes the final `run_complete` log line, returns the HTTP response (including the Google Doc URL) |

(This table was written when `Finalize Run` followed a placeholder Docs Writer.
The placeholder has since been replaced by the real Docs Writer chain — see
[Docs Writer (real Google Docs output)](#docs-writer-real-google-docs-output).)

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
- `Docs Writer (Placeholder)` — logged a `document_write` event. **Since
  removed**: replaced by the real Docs Writer chain (see below).

Later edits, made after the original reliability pass:

- `Strategy Agent` (prompt) — rule (1) extended: the IDs the agent creates in
  its own output (`ICP-`, `PAIN-`, `PILLAR-`, `CHANNEL-`, `PHASE-`, `METRIC-`,
  `RISK-`, `FRQ-`) are labels, never sources, and must not appear in
  `supporting_ids`; the agent must re-scan every `supporting_ids` array before
  finishing and drop anything not starting with `EV-`, `THEME-`, `SWOT-`,
  `ASM-` or `UNK-`. Added because a full run was rejected by the grounding gate
  for citing `CHANNEL-3` (second occurrence of this class of mistake; the
  first was `PHASE-1`, below).
- `Finalize Run` — now returns `document_url` and `rate_limit_errors_logged`
  at the top level of the HTTP response and records both on the `run_complete`
  log line. Verified in a full run (run 5 under
  [Docs Writer test runs](#docs-writer-test-runs)).
- `Init Run Log` — additionally records the server log's byte position at run
  start (`_server_log_offset`, also on the `pipeline_start` line), which
  `Finalize Run` uses for the rate-limit count.

**Retry configuration** (Settings → Retry On Fail; n8n only exposes this on
main-graph nodes, not on `ai_languageModel`/`ai_tool` sub-nodes — see
[Retry semantics](#retry-semantics--what-i-verified) below):

| Node | Max Tries | Wait Between Tries | On Error |
|---|---|---|---|
| Head Planner | 3 | 8000 ms | Stop Workflow |
| AI Agent (Research Agent) | 3 | 8000 ms | Stop Workflow |
| Analyst Agent | 3 | 8000 ms | Stop Workflow |
| Strategy Agent | 3 | 8000 ms | Stop Workflow |
| Docs: Create Document | 3 | 8000 ms | Stop Workflow |
| Docs: Read Back | 3 | 8000 ms | Stop Workflow |
| Docs: Write Content | **off** | — | Stop Workflow |

`Docs: Write Content` deliberately has no retry: it posts a `batchUpdate` whose
first request is `insertText`, which is not idempotent. If a response were lost
after Google had applied the write, a retry would insert the whole document a
second time and the read-back check could still pass. A visible failure is
safer than a silent duplicate. Create and Read Back are safe to retry (Create
at worst leaves one empty stray document; Read Back is read-only).

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
`tool_call`, `validation_gate`, `document_write`, `run_complete`, and
`usage_summary`. The last is not written by the workflow: it is appended by
`n8n/scripts/run_usage.js` after a run (see
[Retry and cost visibility](#retry-and-cost-visibility)).

`client_run_id` is generated at the webhook before the business `run_id`
exists (Head Planner invents `run_id`); every later event carries both, so a
run is traceable end-to-end even if Head Planner's own plan is malformed.

## Docs Writer (real Google Docs output)

Replaces `Docs Writer (Placeholder)`. Five nodes, wired
`Validate For Docs Writer → Build Docs Content → Docs: Create Document →
Docs: Write Content → Docs: Read Back → Verify Document & Log → Finalize Run`.
Node source lives in `n8n/docs_writer/` (the two Code nodes as `.js`, plus
`new_nodes.json`, the bundle used to paste all five into the canvas).

| Node | Type | Job |
|---|---|---|
| `Build Docs Content` | Code | Refuses anything not `status: 'accepted'`. Renders the accepted Strategy artifact into Google Docs `batchUpdate` requests: all 10 required sections, each field followed by its citation tag, then two appendices. Runs the **pre-write** citation checks (below). |
| `Docs: Create Document` | HTTP Request | `POST /v1/documents` with the run's title, via the `Google Docs account` OAuth credential |
| `Docs: Write Content` | HTTP Request | `POST /v1/documents/{id}:batchUpdate` with the built requests |
| `Docs: Read Back` | HTTP Request | `GET /v1/documents/{id}` |
| `Verify Document & Log` | Code | **Post-write** checks against the read-back document, logs the `document_write` result, throws on any failure, returns `document_url` |

**Document layout.** Title, a one-line run header, a "how to read citations"
note, sections 1–10 (ICPs, pains/outcomes, value proposition, positioning,
message pillars, channels, launch phases, success metrics, risks, follow-up
research questions), **Appendix A** (every analyst finding cited, with the
`EV-` ids behind it) and **Appendix B** (every source: `[EV-id]` plus the
`source_title` as a hyperlink to `source_url`). Each field ends with a tag:
the resolving ids (`[THEME-3, SWOT-O1]`), or `[inference]` / `[brief]` when it
has no source. Fields are rendered generically from the artifact, so a field
added to the schema shows up in the document instead of being silently dropped.

**Citation resolution.** The Strategy artifact cites analyst-level ids far more
often than raw evidence ids, so `Build Docs Content` resolves the chain
`supporting_ids → THEME-/SWOT-/ASM-/UNK-/CONF- finding → EV- ids →
source_title/source_url` using the Analyst artifact and the evidence set from
earlier in the same run.

**Pre-write checks (`Build Docs Content`)** — fail the run *before* any Google
API call, so a bad artifact never creates a document:
- input not marked `accepted` by the gate;
- a cited id that resolves to nothing (`Unresolvable citation id(s)`);
- a cited evidence id that is not in the run's evidence set (`Orphaned
  citation(s)`).

**Post-write checks (`Verify Document & Log`)** — run on the document as Google
stored it, not on what we intended to send:
- title matches;
- all 10 numbered section headings are present;
- every planned source id appears in Appendix B;
- every source URL is present as a hyperlink;
- every `EV-xxxxxxxx` string anywhere in the document exists in the evidence
  set (the orphaned-citation check the evidence contract asks the Docs Writer
  to make).

Every outcome is a `document_write` log line (`content_built`, then `ok` or
`error`), including `document_id`, `document_url`, section/source/link counts
and any errors.

**Local tests without n8n or an LLM:** `node n8n/docs_writer/test_local.js
<dir>` runs both Code nodes against a real captured run (artifact, Analyst
artifact, evidence set) with a mock Docs API. 10 checks: 6 on the happy path
plus 4 negative cases — tampered document (missing heading and injected orphan
id), orphaned upstream citation, unresolvable id, and gate bypass. All pass.

**Google auth.** OAuth client and consent screen are described in
`SETUP_DECISIONS.md`. Operational note: the consent screen is in *Testing*
status, so Google expires the refresh token after **7 days**. If the Docs nodes
start failing with an auth error, open the `Google Docs account` credential in
n8n and click *Sign in with Google* again.

**Known limitations.**
- Documents are created private in the authorizing Google account's Drive. A
  reviewer needs the document shared with them (or the account's link
  settings changed) to open the URL in `document_url`.
- Nested bullets (the items under a label such as "Demographic signals") are
  rendered at the same indent as their parent; only the bold label
  distinguishes them.
- Two sources can share one URL (both `https://www.spectrum.com` in the test
  runs), so the number of distinct hyperlinks can be one less than the source
  count. The verifier checks URL membership, not a one-to-one count.

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

## Retry and cost visibility

The guide's Step 8 asks for retry count, token usage and estimated cost per
run. n8n does not make either easy to capture from inside a workflow, so both
are partial. What each does and does not tell you:

### Retries

Retries happen *during* a run: when an agent node throws (for example an
OpenAI 429), n8n waits 8 s and re-runs just that node, up to 3 tries, before
the workflow moves on or stops. LangChain inside the OpenAI node also retries
429s within a single call before n8n sees an error at all.

**There is no true retry count.** n8n stores one execution record per node
with no attempt counter, and downstream Code nodes only ever see the final
result, so a first-try success and a third-try success look identical. This
is an n8n limitation, checked by inspecting stored execution data: per-node
run data has no attempt field (its keys are `startTime`, `executionIndex`,
`source`, `hints`, `executionTime`, `metadata`, `executionStatus`, `data`), and
the execution table's `retryOf`/`retrySuccessId` columns were empty for every
stored execution.

What is recorded instead: **`rate_limit_errors_logged`** on `run_complete` and
in the HTTP response — the number of provider rate-limit (HTTP 429) error
events that n8n printed to `logs/n8n_server.log` between `Init Run Log` and
`Finalize Run`. Limits:
- It **undercounts**: a retry that succeeds silently prints nothing, so only
  failures that reached n8n's console are counted.
- It depends on n8n's stdout being written to `n8n/logs/n8n_server.log` (e.g.
  `./start.sh >> logs/n8n_server.log 2>&1`; `start.sh` itself does not
  redirect). If the file is absent the field is `null`, not `0`.
- The server log has no per-line timestamps, hence the byte-offset approach
  rather than a time window; concurrent n8n activity would be counted too.
- **Verified only partly.** The counting logic was tested against the real
  server log (6 events in the whole file; a slice from a later offset returns
  a smaller count), and run 5 below shows the field reaching the response.
  That run had no rate-limit errors, so the live path returned `0` and has
  **not yet been seen counting a real 429**.

For the n8n-versus-CrewAI comparison, treat "n8n does not expose per-node
attempt counts" as a finding in its own right; CrewAI should record attempts
explicitly.

### Tokens and cost

`n8n/scripts/run_usage.js [execution-id] [--dry-run]` reads one stored
execution from n8n's database (read-only, via the `sqlite3` CLI) and appends a
`usage_summary` event to `runs.jsonl` with per-agent model calls, prompt and
completion tokens, and an estimated cost. It skips an execution that already
has a summary.

It is a separate post-run step because the data can't be reached from inside
the workflow: a probe showed a Code node reading `$('OpenAI Chat Model')`
fails with `No data found from main input` (LLM sub-node output is not on the
main connection), and an execution is only written to the database after the
run finishes.

Pricing is for `gpt-5-mini` at **$0.25 per 1M input tokens and $2.00 per 1M
output tokens**, verified on 2026-09-19 against
<https://developers.openai.com/api/docs/pricing> (standard tier) and recorded
in the script with that date.

**The result is a lower bound, not a bill:**
- n8n reports these counts as `tokenUsageEstimate`, i.e. an estimate from the
  length of the visible text (roughly 4.5–4.8 characters per token in the runs
  inspected), not provider-reported usage.
- `gpt-5-mini` is a reasoning model. Hidden reasoning tokens are billed as
  output but are not visible to n8n, so output cost is understated. (The
  pricing page does not say how reasoning tokens are billed for this model.
  The Analyst step takes about 164 s to emit about 7.2k visible tokens, which
  suggests substantial hidden reasoning; that is an inference, not a
  measurement.)
- Tool-call argument tokens are counted as 0 completion tokens for the
  Research Agent.
- Search-provider fees are excluded. Tavily lists $0.008 per credit with one
  credit per basic search (<https://docs.tavily.com/documentation/api-credits>);
  this was not applied because the number of provider requests per MCP tool
  call was not established.

Estimates from the script (visible-token lower bounds):

| Execution | Outcome | Prompt tok | Completion tok | LLM cost (lower bound) |
|---|---|---|---|---|
| 21 (`r-ocala-001`, earlier success) | success | 178,655 | 12,942 | $0.0706 |
| 23 (`run_oq7f3`) | failed at gate | 811,925 | 12,898 | $0.2288 |
| 24 (`run_oz9f3`) | success | 664,995 | 13,375 | $0.1930 |
| 26 (`client-mu8s7hpa-o2y7m5`) | success | 657,242 | 13,338 | $0.1910 |

Observations from these numbers:
- **The Research Agent dominates.** In executions 23, 24 and 26 it made 14, 13
  and 13 model calls and consumed 585k–732k of the prompt tokens, because the
  agent re-sends its growing context on every tool-call round. In execution 21
  it made only 2 calls (about 107k prompt tokens). Its behaviour varies from
  run to run, so cost does too.
- **A failed run costs about as much as a successful one**, since the
  validation gates sit at the end of the pipeline.
- The lower bounds are well under the brief's $2.50 ceiling, but because they
  omit hidden reasoning and search fees, staying under budget is **not
  demonstrated**. Compare against the OpenAI usage dashboard for the run's
  time window before quoting a cost in the README or KPI table.

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
failed (see [Retry semantics](#retry-semantics--what-i-verified)). Individual
tool-call failures don't retry mechanically, but they also don't restart
anything: they're caught, logged, and the plan continues.

**Is the run time and cost recorded?**
Time: yes, fully — every `agent_end`/`run_complete` event carries
`duration_ms`, and `Finalize Run` computes total wall-clock time and compares
it against the brief's `budget.max_wall_clock_minutes`, flagging (`issues`)
rather than silently overrunning. Cost: estimated, as a **lower bound**, by
a post-run script — `tool_calls_executed` is compared against
`budget.max_search_calls` inside the workflow, and `n8n/scripts/run_usage.js`
turns n8n's stored per-model token counts into a per-agent and total dollar
estimate (`usage_summary` event). It is not a measured bill: n8n's token
counts are character-based estimates that omit hidden reasoning tokens, and
search-provider fees are excluded. See
[Retry and cost visibility](#retry-and-cost-visibility). `budget.max_cost_usd`
is carried into every `run_complete` line but is **not enforced** against
either figure.

**Does the Docs Writer reject incomplete or invalid content?**
Yes, demonstrated live, twice: `Validate For Docs Writer` rejected the first
failed run below with a specific, itemized reason (unresolved `PHASE-1`
citation) before the Docs Writer ever ran, and rejected the first Docs Writer
full run for an unresolved `CHANNEL-3` citation (see
[Docs Writer test runs](#docs-writer-test-runs)). Behind the gate, `Build Docs
Content` independently refuses anything not marked `status: 'accepted'`,
rejects unresolvable and orphaned citations before calling Google, and
`Verify Document & Log` re-checks the stored document after writing.

## Test run (success, placeholder Docs Writer)

Historical: the run that validated the reliability layer, before the real Docs
Writer existed. The current end-to-end result is in
[Docs Writer test runs](#docs-writer-test-runs).

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

## Docs Writer test runs

Run in this order, deliberately: cheap isolated tests first, then full runs.

**1. Local harness (no n8n, no network, no LLM cost).** 10/10 checks pass — see
above.

**2. Isolated n8n run.** A throwaway workflow (since deleted) fed the five real
Docs Writer nodes from the stored data of an earlier successful execution, via
three stub nodes named like the real upstream nodes. Real credential, real
Google Docs API, no LLM calls. `client_run_id: isolated-docs-writer-test`.
Result: document created, 10 sections, 13 sources, 12 distinct hyperlink URLs
(two sources share one URL), 0 orphaned evidence ids, post-write check 3.8 s.
The resulting document was opened in Google Docs and inspected: real
headings, bold labels, bullets, inline citation tags, and working hyperlinks in
Appendix B. Its log lines remain in `runs.jsonl`.

**3. Full run — rejected by the grounding gate.**
`client_run_id: client-mu8q669f-oqaesp` / `run_id: run_oq7f3`. All four agents
completed (Head Planner 29.5 s, Research Agent 107.7 s, Analyst 189.3 s,
Strategy 77.7 s). `Validate Strategy Grounding` and `Validate For Docs Writer`
rejected the output:
`unresolved_supporting_ids: [{"path":"launch_phases[1].activities[0]","id":"CHANNEL-3","basis":"evidence"}]`.
The Strategy Agent cited one of its own channel ids as evidence — the same
class of mistake as the `PHASE-1` failure above, despite the prompt already
forbidding invented ids. The Docs Writer never ran; HTTP 500 after 404 s. No
document was created.

**Fix:** the Strategy Agent prompt was extended (see "Later edits" above).
The gate itself was not loosened, and no auto-repair of bad ids was added: a
gate that silently strips citations would defeat the point of the check.

**4. Full run — success.**
`client_run_id: client-mu8qz6yb-1y7fn0` / `run_id: run_oz9f3`.

- HTTP 200, `status: "success"`, `issues: []`
- Total 423.9 s (7 min 4 s) against the 12-minute budget
- Head Planner 35.1 s; Research Agent 82.2 s; Analyst 229.8 s; Strategy 73.4 s
- 3/3 research questions answered; 206 evidence records; 0 invalid URLs
- 12 of 13 planned tool calls executed (the budget check only flags overruns;
  why one planned call did not execute was not investigated)
- Evidence coverage 100%; strategy grounding clean
- Docs Writer: content built in 20 ms; document created and verified in 3.4 s;
  13 sources, 12 distinct links, 0 orphaned evidence ids, 0 errors
- Document title carries the run id (`Wire3 GTM Plan - run_oz9f3 - …`) and was
  opened to confirm it is this run's content

Caveats on this result: it is one successful run after one failure, so it shows
the pipeline can pass, not that the Strategy Agent will never repeat the
citation mistake — the gate is what guarantees a bad artifact cannot become a
document. Dollar cost was not measured at the time of this run; a visible-token
lower bound of $0.193 was added afterwards by `run_usage.js` (see
[Retry and cost visibility](#retry-and-cost-visibility)), and
`budget.max_cost_usd` is recorded but not checked against real spend.

**5. Full run — verification of the retry/usage/URL changes.**
`client_run_id: client-mu8s7hpa-o2y7m5` (execution 26), run after the
`Finalize Run` / `Init Run Log` edits and with the same brief.

- HTTP 200, `status: "success"`, `issues: []`, 443.7 s (7 min 24 s)
- Response now contains `document_url` (a new Google Doc, verified by the
  `Verify Document & Log` check: 0 errors, 3.6 s) and `rate_limit_errors_logged: 0`
- `pipeline_start` logged `server_log_offset: 23390`
- 3/3 research questions answered; 148 evidence records; 12/12 planned tool
  calls executed; 0 invalid URLs; evidence coverage 100%; grounding clean
- `run_usage.js` for this execution: 657,242 prompt / 13,338 completion tokens,
  visible-token lower bound $0.191

The `0` for rate-limit errors is expected for a run with no provider errors; it
shows the field is populated end to end, not that the counter detects a real
429 in a live run.

Raw log lines: `n8n/logs/runs.jsonl`, filtered by each `client_run_id` above.

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

`n8n/workflows/wire3_gtm_pipeline.json` is the current export, re-exported
after the Docs Writer work and the `Finalize Run` change (via the n8n CLI,
`n8n export:workflow`, keeping the same top-level shape as the earlier UI
export). Verified secret-free: a `grep` for OpenAI-style `sk-…` keys, Google
`GOCSPX`/`ya29.`/refresh-token prefixes, and `client_secret`/`clientSecret`/
`apiKey`/`api_key` in the workflow and `docs_writer/` files returns nothing.
The only `credentials` entries are `{id, name}` references: the "OpenAI
account" object (the real key is injected at process start via
`CREDENTIALS_OVERWRITE_DATA` in `start.sh`, from the gitignored `.env`) and the
"Google Docs account" object (its client id/secret and tokens live in n8n's
own encrypted credential store, in the gitignored `n8n/.n8n/` folder — see
SETUP_DECISIONS.md). Neither ever touches the exported JSON.
