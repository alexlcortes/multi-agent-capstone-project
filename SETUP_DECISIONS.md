# Environment & Infrastructure Decisions

Running log of setup decisions made outside the schema contract, for the final README (guide Step 12, items 4/5/9). Each entry: what was decided, why, and what it costs us if we're wrong.

## Search provider: SerpAPI + Tavily, both behind one adapter

**Decision:** implement one search-provider interface in the MCP layer; both SerpAPI and Tavily are real, working implementations of it. Tavily is the default during development/testing; SerpAPI is used for final graded runs, since the capstone brief explicitly names it.

**Why:** the capstone brief names SerpAPI specifically, but the course demos build against Tavily, and this project is self-funded (personal OpenAI/SerpAPI keys, not course-VM credentials). SerpAPI's free tier is a one-time ~100 searches before billing kicks in (~$50/mo for 5k after that); the brief's own per-run budget allows up to 40 search calls, so 2–3 development runs would exhaust it. Tavily's free tier (~1,000 requests/month) makes iterative development affordable without touching the SerpAPI quota at all.

**Cost if wrong:** if the adapter silently only calls Tavily while claiming SerpAPI support, that's a real compliance gap the guide explicitly warns against ("do not silently claim SerpAPI support when the implementation only calls Tavily"). Mitigated by keeping both implementations real and documenting which one is active per run in the run-record log (Phase 7).

## Credential storage: environment variables (`.env`), not n8n's built-in credential store

**Decision:** API keys (OpenAI, SerpAPI, Tavily) live in `.env` files (gitignored), loaded as environment variables. n8n's native credential store is used only where n8n *requires* it structurally (its OpenAI/Google OAuth credential objects, which nodes reference by name) — but the underlying values are still sourced from environment variables via n8n's `$env` expressions / credential-from-env pattern wherever the node type allows it, not typed in by hand into the UI.

**Exception — the Google OAuth client secret (updated when Docs Writer was built):** it is *not* in any `.env`. The client ID and secret were entered directly into n8n's `Google Docs account` credential form, and n8n stores them (and the resulting access/refresh tokens) encrypted in its own database under the gitignored `n8n/.n8n/` folder. OAuth tokens have to live in n8n's store anyway for its automatic refresh, and there was no environment-variable route for the client secret in this setup. Consequence: the Google secret does not travel with the repo — a fresh checkout has to repeat the OAuth setup below and re-create that credential. Google shows a client secret in full only at creation, but the console lets you add a new secret later if it is lost.

**Why:** n8n's credential store is encrypted at rest and fine for a single local instance, but it's UI-managed state that doesn't show up in the exported workflow JSON or in git — meaning it doesn't travel with the project the way a documented `.env.example` does, and it's a second place secrets could end up (screenshots of the n8n UI, exported workflow JSON if credentials get inadvertently embedded). Environment variables are the same mechanism used on the CrewAI side, so both implementations' secret-handling story is consistent and equally easy to describe in the README. Tradeoff: n8n's credential store gives you per-credential OAuth refresh handling for free (relevant for Google Docs, see below) — so this is genuinely a "use env vars for API keys, use n8n's store only for OAuth objects" split, not a pure either/or.

## Logging destination: local structured JSON Lines file, not a hosted service

**Decision:** every agent/tool/document-write event (per the Phase 7 run-record schema) is appended as one JSON object per line to a local `logs/runs.jsonl` file, per implementation (`n8n/logs/runs.jsonl`, `crewai/logs/runs.jsonl`). No external logging service (LangSmith, Firestore, etc.) for the graded submission.

**Why:** the capstone needs run records comparable across two independently-built implementations and reviewable by someone else (a grader) without needing an account on a third-party observability platform. A flat JSONL file is trivial to `grep`/`jq`, attach to the submission, and diff between the n8n and CrewAI runs. The course demos' LangSmith/Firestore examples are good references for the *shape* of what to log, not a requirement to actually run through those services.

**Cost if wrong:** if per-run logging volume or the need for a live dashboard grows, this doesn't scale past local development — acceptable here since the deliverable is a bounded, ≤12-minute, ≤$2.50 run, not a production service.

## Google Docs OAuth: deferred during environment setup, completed with the Docs Writer build (P6.2)

**Decision:** Google Cloud Console project creation, OAuth consent screen, and the Docs/Drive API-scoped OAuth client were deferred from Step 3's environment pass and set up when the Docs Writer step was built. **Status: done and verified** — the n8n credential shows "Account connected", and two full-document writes succeeded through it (an isolated test and a full pipeline run).

**Why deferred:** OAuth setup is the most failure-prone, order-dependent step in the whole guide (redirect URI must be known from n8n *before* creating the Google OAuth client, or you hit `redirect_uri_mismatch`) and it exists to serve one specific node. Setting it up early would have meant debugging OAuth with nothing downstream to test it against. Sheets API is confirmed optional by the expanded guide and was skipped.

**What was set up (so it can be reproduced):**
- Google Cloud project `wire3-gtm-capstone`, created with no parent organization.
- APIs enabled: **Google Docs API** and **Google Drive API** only.
- Google Auth Platform / consent screen: app name "Wire3 GTM Capstone", user type **External**, publishing status **Testing**, the developer's own Google account as support contact, contact email and the only **test user**. External was the only option because the project has no organization; the "Internal" type needs a Workspace org.
- OAuth client: type **Web application**, name "n8n Wire3 Docs Writer", authorized redirect URI `http://localhost:5678/rest/oauth2-credential/callback` (n8n's default callback for a local instance — it matches the redirect URL n8n displays in the credential form).
- n8n credential: "Google Docs account" (Google Docs OAuth2 API), client ID/secret pasted in, then *Sign in with Google*. Scopes are n8n's defaults for that credential type (the "Custom Scopes" toggle was left off).

**Cost if wrong / things to know:**
- **Refresh tokens expire after 7 days** while the consent screen is in Testing status. A run more than a week after the last sign-in can fail with an auth error; the fix is to open the credential in n8n and click *Sign in with Google* again. Publishing the app would lift this, but it invites Google's app-verification process and isn't worth it for a bounded capstone. **Re-authorize before any graded run.**
- Only the listed test user can authorize, and the redirect URI is localhost, so this setup works only for a locally running n8n. Hosting n8n elsewhere means adding that host's callback URL to the client.
- Google may take a few minutes to apply a newly added redirect URI; a fresh `redirect_uri_mismatch` right after creating the client is worth waiting out before debugging.
- During sign-in Google shows an "unverified app" warning in Testing mode; that is expected.

## Cost and retry tracking (n8n): post-run script and a server-log count, both approximate

**Decision:** token usage and estimated cost are computed after each run by `n8n/scripts/run_usage.js`, which reads the stored execution from n8n's database and appends a `usage_summary` event to `runs.jsonl`. Retries are approximated inside the workflow by `rate_limit_errors_logged`: the number of HTTP 429 events n8n printed to `logs/n8n_server.log` during the run. There is no true per-node retry count.

**Why:** the guide asks for retry count, token usage and estimated cost per run. In n8n, LLM sub-node token data is unreadable from Code nodes (tested: `No data found from main input`) and stored executions carry no attempt counter, so a workflow node cannot capture either. A post-run script and a log-offset count were the least invasive options that produce a real number.

**Cost if wrong:** both figures are approximations and are labelled that way in the log. The cost is a **lower bound**: n8n's token counts are character-based estimates that exclude hidden reasoning tokens, and search-provider fees are not included. Prices in the script (gpt-5-mini, $0.25 / $2.00 per 1M input/output tokens, verified 2026-09-19) must be updated by hand if they change. The 429 count undercounts silently-successful retries and only works if n8n's output is redirected to `n8n/logs/n8n_server.log`. `budget.max_cost_usd` is recorded but not enforced. The CrewAI implementation should log attempts and provider-reported usage directly, so the two runs are compared with the limitation stated. Before quoting cost in the README, check the OpenAI usage dashboard for the run's time window.

## Docs Writer: Google Docs REST API via HTTP Request nodes, not a single built-in node

**Decision:** the Docs Writer creates the document with `POST /v1/documents` and fills it with one `documents.batchUpdate` call, using n8n's HTTP Request node with the `Google Docs account` OAuth credential, rather than a single n8n Google Docs node.

**Why:** one `batchUpdate` request list can insert all text and apply heading styles, bullets, bold labels and hyperlinks (source titles as clickable links) in a single atomic write, and the layout logic stays in a Code node that can be unit-tested locally with no Google account (`n8n/docs_writer/test_local.js`). The built-in Google Docs node was not evaluated in depth, so this is a chosen approach, not a proven necessity.

**Cost if wrong:** the raw API calls are more code to maintain than a built-in node would be, and the retry settings have to be set by hand per node (the write step is deliberately left without retry, because re-sending `insertText` would duplicate the document; see `n8n/RELIABILITY_AND_VALIDATION.md`).

## Docs Writer output: private documents in the developer's Drive

**Decision:** each run creates a new Google Doc in the authorizing account's Drive and returns its URL (`document_url`) in the HTTP response and the run log. No sharing settings are applied.

**Why:** setting a sharing policy is a separate, consequential choice (it exposes generated content and, via Drive, could reach people the developer didn't intend), and no requirement for it was found while building. The private default is the safe one.

**Cost if wrong:** a grader or teammate opening `document_url` will get an access request until the document is shared. Decide how the deliverable reaches reviewers (share the document, export it to PDF, or attach it to the submission) before submission. Every full run also leaves one more document in Drive, so test runs need occasional manual cleanup.

## CrewAI: the "6 LLM calls per role" budget counts reasoning calls, not per-tool steps

**Decision:** in the CrewAI implementation, the brief's cap of 6 LLM calls per agent role is applied to an agent's reasoning and planning calls. The Research Agent's one-LLM-step-per-tool-call loop is treated as tool execution and is bounded by the plan instead (at most `max_search_calls`, 40). Research's `max_iter` is 50, the same as n8n's Research node. Head Planner, Analyst and Strategy keep `max_iter=6`. Actual LLM calls per role are counted from `LLMCallStartedEvent` (`wire3_gtm/verify_handoff.py`) and reported, not assumed.

**Why:** an agent that calls tools makes one LLM request per tool call plus a final one, so a 14-call plan takes 15 requests (measured on 2026-09-20: Head Planner 1, Research 15). A literal cap of 6 stopped Research after about 6 of 15 planned calls (`max_iter=6` gave 6/15 executed), which silently truncated the evidence set. n8n's Research Agent has the same per-call pattern with `maxIterations` 50, so this keeps the two implementations comparable.

**Cost if wrong:** the brief's per-role budget is not literally met for Research: 15 LLM calls against a cap of 6. The comparison must state this for both implementations. If a grader reads the cap literally, the alternatives are issuing the plan's calls in parallel within one or two LLM steps, or executing the plan in code with the agent only reporting. `max_iter` is a ceiling, not a counter, and nothing yet fails a run that goes over 6 reasoning calls.

## CrewAI Docs Writer: added after the text-only flow was shown stable, with its own Google credentials

**Decision:** the CrewAI Docs Writer (`wire3_gtm/docs_content.py`, `docs_google.py`, `pipeline.run_docs`) is a separate milestone that reads a finished run's saved artifacts. It was built only after two fresh end-to-end runs (Planner→Research→Analyst→Strategy) completed cleanly. "Clean" meant every step `ok`, no salvage, and under the brief's 12 minutes: run `run-20260920-211110` took 7.6 min (Analyst 2 attempts), run `run-20260920-211852` took 9.4 min (Analyst 3 attempts). The one earlier full run had failed in the Analyst after 11.7 min; that failure (an invented evidence id) is what `drop_invented_ids` now handles.

**Why last:** the guide says not to let OAuth block the project and to inspect the validated Strategy output as local JSON or Markdown first; the Docs Writer is a renderer, not one of the four agents; and it must only receive validated structured content. A document write also has side effects a text run does not: each run creates a real Google Doc, and an automatic retry could duplicate it.

**How it differs from the n8n Docs Writer:** it also renders the Analyst's tables (competitor and feature comparison, pricing matrix, themes, SWOT, 7P, assumptions/unknowns/conflicts) and a research-scope section with evidence-quality caveats (top-tier source share, unsupported numbers, dropped ids, salvage). The brief lists these sections as required; the n8n document renders only the Strategy artifact. Tables are rendered as labelled bullet lists, as in n8n, not Google Docs tables.

**Credentials (CrewAI cannot use n8n's):** the Google OAuth token in n8n lives in n8n's encrypted store. CrewAI needs its own: create an OAuth client of type **Desktop app** in the same Google Cloud project (Docs and Drive APIs are already enabled), download it to `crewai/.google/client_secret.json` (gitignored), then run `uv run python -m wire3_gtm google-auth` once (opens a browser); the token is saved to `crewai/.google/token.json` (gitignored). Scopes are `documents` and `drive.file` (only files this app creates, enough to create the Doc and export a PDF). If the consent screen is in Testing mode, refresh tokens expire after 7 days and `google-auth` must be rerun.

**Reliability choices:** the document id is saved the moment the Doc exists, and a rerun verifies that document instead of creating another. `batchUpdate` is deliberately not retried (an ambiguous failure could insert the text twice); create, read and export retry up to 3 times. Before anything is written the content is re-validated with the same checks as the guardrails, and the requests are applied to a simulated document (UTF-16 indexing, as Google uses) and verified locally.

**Cost if wrong:** the Google path has been tested only against a fake client and a simulated read-back, not a live Google account, until `google-auth` has been run. The post-write check confirms headings, source ids, hyperlinks and orphaned ids; it does not check that the text reads well.

## CrewAI logging, retries, budget and cost: same schema as n8n, measured instead of estimated

**Decision:** CrewAI writes `crewai/logs/runs.jsonl` in the same event schema as `n8n/logs/runs.jsonl` (`pipeline_start`, `agent_start/end`, `tool_call`, `validation_gate`, `document_write`, `run_complete`, `usage_summary`), with `implementation: "crewai"`. `python -m wire3_gtm compare` prints the latest run of each side by side. A `run_complete` is written however a run ends (`success`, `degraded`, `failed`, `stopped_budget`).

**How it differs from n8n, and why the comparison must say so:**
- **Tokens and cost** are provider-reported per LLM call and include hidden reasoning tokens (a one-word "pong" call spent 64 of its 74 output tokens on reasoning). n8n's counts are character estimates without reasoning, so its cost is a lower bound and CrewAI's is exact for the LLM. Prices are copied from `n8n/scripts/run_usage.js` (verified there 2026-09-19) and were not re-verified. Search-provider fees are excluded on both sides.
- **Retries** are counted, in three kinds: search-tool attempts beyond the first, guardrail re-prompts, and the OpenAI SDK's own transient retries. n8n's figure is the number of HTTP 429s in a server log.
- **Links** are checked with a real HEAD request through the MCP `validate_source` tool. Only a 404/410 or an unresolvable hostname counts as broken; 403/429 and similar are `blocked` (the page exists) and timeouts, resets and 5xx are `unverified`. My first version counted timeouts as broken and marked a healthy run degraded; three "broken" links were timeouts, and two of those pages answered a normal request with 403. n8n's `invalid_url_count` only checks URL format.

**Retries and backoff:** search calls retry transient failures (provider errors, timeouts, 429/5xx) up to 3 attempts with 2 s and 4 s backoff; bad arguments are not retried. Every attempt counts against the search budget, and only the final result becomes evidence, so a retry cannot create duplicate evidence. The Google `batchUpdate` is never retried (see the Docs Writer decision).

**Budget enforcement** (limits come from the brief). CrewAI: search calls are refused once 40 are used; wall-clock time and cost are checked between steps, after every rejected draft, and before every search call, and a breach stops the run as `stopped_budget` with its artifacts kept, so it can be resumed. Every LLM reply is capped at `max_completion_tokens_per_call` (32k, reasoning included), which bounds how far one call can overshoot a limit. n8n: every chat model has the same 32k cap (`maxTokens`), and a budget guard at the end of the Head Planner, Research, Analyst and Strategy steps stops the run with a `BUDGET:` error when the wall clock or search-call limit is passed (`run_usage.js` records it as `stopped_budget`). n8n cannot stop an agent mid-loop or read token costs during a run, so its search cap is checked after Research and its cost cap only after the run. For testing, `run --budget max_cost_usd=0.02` (CrewAI) and `run_pipeline.js --budget max_wall_clock_minutes=1` (n8n) tighten one limit for one run without changing the brief; the override is listed as an issue.

**Budget stop, tested live** (`run-20260922-225640`, cost cap $0.02): the run stopped as `stopped_budget` before the Analyst step, at $0.030. The overshoot is inherent: a limit can only be checked between LLM calls, and the Research Agent had issued all 15 searches in its first LLM step, so the per-search check had nothing left to refuse and the step boundary did the stopping. The plan, 427 evidence records and the tool-call log were kept, and the run record carries the stop reason.

**n8n budget stop, tested live** (execution 31, `client-mudjyvum-ivex3t`, wall clock tightened to 1 min): the guard after the Research Agent stopped the run with `BUDGET: wall-clock budget reached after Research Agent: 1.7 min elapsed, limit 1 min`, recorded as `stopped_budget` with the override listed as an issue. The first attempt (execution 30) never reached the guard: OpenAI's 500k tokens-per-minute limit returned a 429 mid-Research and n8n's model node gave up after its default 2 retries. The n8n Research Agent resends its whole conversation (~128k tokens by the end) on every LLM call, and cached searches remove the pauses between calls, so the cache made this likelier. All four model nodes now retry up to 6 times (`maxRetries`, exponential backoff in the OpenAI client); execution 31 hit no 429, so the higher retry count is configured but not yet seen working live. The same run confirmed the 32k `maxTokens` cap is accepted for gpt-5-mini.

**Caching: in the MCP server, with the original timestamps** (reverses the earlier "no cache" decision, which the guide's reliability step asks for). `mcp-server/research/cache.py` caches each successful, non-empty search by (provider, query, max_results) for `RESEARCH_CACHE_TTL_HOURS` (24; 0 turns it off), so n8n and CrewAI share it. A hit returns the results exactly as first retrieved, with their original `retrieval_timestamp`, so evidence still says when the page was read, plus `from_cache`/`cached_at`; CrewAI logs `from_cache` per tool call and counts `tools.cache_hits` in the run record (n8n's workflow does not record it yet). Errors and empty answers are never cached. A cache hit still takes a search slot in CrewAI's budget (conservative). Within a run, evidence is also saved once and reused on resume.

**Uncited claims are flagged, not silently accepted:** a claim with no source must say so (`basis: inference`, shown as `[inference]` in the document). The run record's `claims` block counts evidence / brief / inference claims and the uncited share in both implementations, and CrewAI lists the count as an issue.

**Cost if wrong / known limits:** the per-role "6 LLM calls" cap is reported but Research is exempt (see the earlier decision). The 12-minute budget is tight: the Analyst alone takes 5 to 7 minutes and retries can push a run past it. The compare table is only fair if both implementations run the same brief: the recorded n8n runs used a 3-question brief and the CrewAI runs use all 8.

## CrewAI orchestration: a crewai.Flow over typed state, and validator-free "wire" models for the LLM call

**Decision (Flow):** the pipeline runs as a `crewai.Flow` (`crewai/wire3_gtm/flow.py`): one `@start` / `@listen` step per role (research, analysis, strategy, link check, document), with the shared artifacts (`ResearchPlan`, evidence list, `AnalystArtifact`, `StrategyArtifact`) held as typed Flow state. The step bodies are ordinary functions in `pipeline.py` that also save every artifact to the run store, so a failed run still resumes from disk. `run_pipeline()` keeps its signature and simply kicks off the Flow, which is why the existing tests exercise it unchanged. The guide asks for a Flow that "passes the shared artifacts between roles"; this is that, and `pipeline_start` records `orchestrator: crewai.Flow`.

**Decision (wire models):** the LLM call is given a validator-free twin of each contract (`wire_models.wire_model`), and the strict model validates the reply in the task guardrail. **Why:** CrewAI's OpenAI path parses the reply with `chat.completions.parse(response_format=Model)`, which runs inside the OpenAI SDK. A custom validator that rejected the reply (a Wire3 pricing row with a short promo and no sourced post-promo price) raised out of the LLM call: no guardrail feedback, no retry, no saved draft, and a 9-minute Analyst step lost (run `run-20260920-230711`, still in `crewai/logs/runs.jsonl` as a failed run). The wire twin has the same fields, types and Field constraints, and the generated JSON Schema is identical (asserted in `tests/test_wire_models.py`), so constrained decoding is unchanged; only the custom rules moved. Verified live: in `run-20260920-232205` a rule violation arrived as guardrail feedback, the model corrected it on the next attempt, and the run succeeded.

**Cost if wrong:** structural constraints that OpenAI does not enforce during decoding (for example string length) can still raise inside the SDK; that path is unchanged and has not failed in any recorded run. Each guardrail retry costs about 2 minutes and about $0.05 of Analyst tokens, and the 12-minute budget leaves room for roughly two.

## Same-brief comparison (8 questions), n=1 run per implementation

Both implementations were run on the same 8-question brief and budget (n8n via its editor's test webhook with a payload derived from `crewai/brief.json` but not identical to it -- it added `wire3_facts`, research-question priorities and reworded fields, found by the run_record brief hash (`schemas/run_record.md`); since then both read the repo-root `brief.json`, n8n via `n8n/scripts/run_pipeline.js`; the earlier recorded n8n runs used a different 3-question brief and are not comparable).

| | n8n (`run-9f4b2a`) | CrewAI Flow (`run-20260920-232205`) |
|---|---|---|
| Latency | 4.1 min | 11.6 min |
| Analyst | 113 s, 1 LLM call | 509 s, 4 LLM calls (3 guardrail rejections) |
| LLM cost | $0.2006, a lower bound (no reasoning tokens) | $0.2041, provider-reported incl. reasoning |
| Questions answered / evidence | 8/8, 206 records | 8/8, 338 records |
| Broken links | not checked (URL format only) | 0 of 30 cited, HEAD-checked |

**How to read it:** the latency gap is mostly the extra Analyst attempts, not the framework. Per LLM call the Analyst takes about the same (113 s vs 127 s). CrewAI's guardrails reject drafts that n8n's gates accept: n8n's Analyst artifact, run through CrewAI's checks, has 8 violations (service/technology types written as free text instead of `cable | fiber | dsl | fixed_wireless`, an inferred post-promo price, a 82-character theme title), and against the original shared `analyst_artifact.schema.json` it has 2 (a pricing matrix with 3 rows where the schema requires 4, and the same over-long title). n8n's prompt tells it Wire3 need not be priced, which conflicts with the schema's `minItems: 4`. So the two implementations are not enforcing the same contract, and the stricter one is slower.

## CrewAI Analyst speed: coverage judged per search result, a 3-row pricing minimum, code repairs, and a time-aware degrade

**Problem:** the Analyst step was the latency risk (5 to 9 minutes; a full run took 10.4 to 11.6 of the 12 allowed). In all 12 saved first drafts the first attempt was rejected on research-question coverage, and each rejection cost a full Analyst call (2 to 3.5 minutes).

**Decision 1: coverage is judged per search result, not per evidence id.** The evidence set holds one copy of each search result per research question its search call served, each with its own id (2.8 to 3.1 copies per result). The old rule required the copy tagged with that question's id, so the Analyst was rejected for citing the right page under a sibling copy. A question now counts as covered if any copy of a result returned for it is cited. Replayed on the 12 saved first drafts, 12 failed the old rule and 0 fail the new one. It still rejects a question none of whose results are cited and it still refuses "unknowns" as an escape for a question with 5+ records. **Cost if wrong:** it is more lenient, and result tags come from the search call, not relevance. Depth per question is recorded (`rq_source_depth`) and a question backed by fewer than 3 distinct cited sources is listed as an issue in the run summary; in the first full run after this change RQ5 was backed by 1 source and RQ4 by 2.

**Decision 2: `pricing_matrix` needs 3 rows, not 4.** This deviates from `analyst_artifact.schema.json` (`minItems: 4`, one row per competitor including Wire3). Wire3's price is not public and the brief forbids inventing it, so a priced Wire3 row usually cannot exist; n8n's artifact already has 3 rows. Each priced competitor must still have a row. A test states the deviation.

**Decision 3: two pricing violations are repaired in code and reported, never re-prompted.** (A) A Wire3 pricing row without an evidence-cited price is dropped. (B) An inferred numeric post-promo price on a promo of 12+ months is set to null (its weight in the 12-month blend is zero). A repair only removes a row or a guess; it never adds or changes a claim. Not repaired: an inferred price on a no-promo or short-promo row, where nulling leaves an unpriceable row; those still go back for a retry. **Caveat:** in the last measurement the repairs never fired (the prompt change and the minimum removed the Wire3 rows); replayed on the previous round they would have saved 2 of 4 retries.

**Decision 4: time-aware degrade.** If the only remaining problem is uncovered questions and another Analyst attempt plus the steps after it (reserve 100 s) will not fit in the time budget, the draft is accepted, the run is reported `degraded`, and the document names the gap. Contract violations, ungrounded citations and non-JSON are never accepted this way. Mechanically tested and seen once live; it has not yet been needed in a run.

**Measured (Analyst step alone, 4 trials per round on the same two saved evidence sets):** mean time 511 s, then 294 s, then 205 s; mean attempts 3.5, 2.0, 1.2; mean cost $0.118, $0.057, $0.039. A full run took 7.1 minutes. Small samples; expect variance. The first attempt at this fix (a per-question checklist and RQ ordering in the prompt) did nothing measurable.

**Decision 5: the plan is enforced in code.** The Research Agent is asked to make every planned call and sometimes does not (it skipped 2 of 15 in one run, which was then reported as success). After the agent finishes, the pipeline runs any planned call with no matching recorded call, through the same wrappers (retries with backoff, the search budget, evidence recording), and marks each one `source: "enforced"` in the tool-call log so the record still shows the agent skipped it. Matching is exact on tool and arguments (ignoring case, a null region and `max_results`); a call with different arguments does not count as the planned one. A recovered skip is listed as an issue but does not degrade the run; a skip that could not be recovered still does. **Caveat:** tested offline (10 tests); the run that followed did not need it (the agent made all 16 calls), so it has not been seen working live.

**Known gaps, not yet fixed:** Strategy once needed a retry because a follow-up question listed an assumption id (`ASM-1`) where only `UNK-n` ids are allowed. With no evidence-cited post-promo price the pricing matrix can carry no post-promo prices at all, which weakens the promo-cliff comparison.
