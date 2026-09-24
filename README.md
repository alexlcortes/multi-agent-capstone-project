# Multi-Agent Market Research & GTM Planning: Wire3 (Ocala, FL)

Two implementations of the same four-agent pipeline, one in **n8n** and one in **CrewAI**. Each takes one
market brief, researches it through a shared **MCP research server**, and writes a cited go-to-market plan
to **Google Docs** (with a PDF export). The two were run on the same brief and measured against the same KPIs.

Contents: [1. Goal and brief](#1-goal-and-brief) · [2. Architecture](#2-architecture) ·
[3. Agent roles](#3-agent-roles) · [4. MCP tools and search provider](#4-mcp-tools-and-search-provider) ·
[5. Environment setup](#5-environment-and-credential-setup) · [6. Run n8n](#6-how-to-run-the-n8n-workflow) ·
[7. Run CrewAI](#7-how-to-run-the-crewai-uv-project) · [8. Docs and PDF](#8-google-docs-and-pdf-export) ·
[9. Logging, retry, budget, caching](#9-logging-retry-budget-and-caching-decisions) ·
[10. Tests](#10-unit-and-scenario-tests) · [11. KPI results](#11-kpi-results) ·
[12. n8n vs CrewAI](#12-n8n-versus-crewai) · [13. Limitations](#13-known-limitations-and-future-improvements) ·
[14. Troubleshooting](#14-error-resolutions-and-troubleshooting)

---

## 1. Goal and brief

**Goal:** build the same multi-agent research-and-strategy system twice, once as a visual n8n workflow and once
as a code-first CrewAI project, and compare them with measurements rather than impressions.

**Brief** (full text: [`PROJECT_BRIEF.md`](PROJECT_BRIEF.md); machine-readable, shared by both implementations:
[`brief.json`](brief.json)): a go-to-market plan for **Wire3**, a regional fiber ISP, to win **price/value
switchers** in **Ocala / Marion County, FL** from **Spectrum, AT&T and T-Mobile Home Internet**. Wire3 can't
compete on a mobile bundle or on brand recognition. The brief sets 8 research questions, 13 required output
sections and a per-run budget of **12 minutes, 40 search calls, 6 LLM calls per role and $2.50**.

**Academic boundary:** Wire3 is a real company (the author's employer). Only facts from Wire3's public website
are used, and every output is framed as a hypothetical capstone exercise, not Wire3's actual plan. See
`PROJECT_BRIEF.md` §9.

## 2. Architecture

```
                         brief.json (shared)
                               │
            ┌──────────────────┴──────────────────┐
            ▼                                     ▼
   n8n workflow (webhook)               CrewAI Flow (python -m wire3_gtm run)
            │                                     │
            ▼                                     ▼
   ┌───────────────── the same 4 roles, same artifact contracts (schemas/) ─────────────────┐
   │ Head Planner ──► Research Agent ──► Analyst Agent ──► Strategy Agent ──► Docs Writer   │
   │  ResearchPlan     EvidenceSet        AnalystArtifact   StrategyArtifact   Google Doc   │
   │                        │                                                 + PDF        │
   │                        ▼                                                              │
   │          MCP research server (mcp-server/, streamable HTTP :8000/mcp)                 │
   │          5 research tools + validate_source · 24 h cache · provider adapter           │
   │                        │                                                              │
   │                 Tavily  or  SerpAPI                                                   │
   └────────────────────────────────────────────────────────────────────────────────────────┘
            │                                     │
   n8n/logs/runs.jsonl                   crewai/logs/runs.jsonl      (same run-record schema)
```

- **Validation between every step.** Each hand-off is a structured artifact checked against a contract in
  [`schemas/`](schemas/): evidence ids must exist, every research question must be covered, and inferred
  values are marked `[inference]`. A run that fails a check is repaired, re-prompted or stopped. It never
  passes on silently.
- **Evidence is built in code** from raw tool output, never written by the LLM, so every citation traces to
  a real search result with its URL and retrieval timestamp.
- **Layout of the repo:**

| Path | What |
|---|---|
| `brief.json`, `PROJECT_BRIEF.md` | The shared brief |
| `schemas/` | Artifact contracts (JSON Schema + notes), Docs field map, document template |
| `mcp-server/` | MCP research server (Python, uv) |
| `n8n/` | Workflow export, start script, run and export scripts, Docs Writer code + tests |
| `crewai/` | CrewAI UV project (`wire3_gtm` package, YAML agents/tasks, tests) |
| `samples/` | Generated GTM plans as PDFs (two CrewAI runs, one n8n run), plus Markdown and Doc verification record |
| `screenshots/` | CrewAI end-to-end run in the terminal (`crewai/`) and the n8n workflow canvas (`n8n/`) |
| `snapshots/` | Committed, hash-verified copy of one validated run's artifacts |
| `eval/` | KPI report, comparison table and log, rubric, blind reviews, A/B test |
| `SETUP_DECISIONS.md` | Every environment/design decision with its cost if wrong |

## 3. Agent roles

| Role | Job | Output | Guardrails |
|---|---|---|---|
| **Head Planner** | Turns the brief into a bounded plan: which MCP tool, for which company, answering which research question | `ResearchPlan` | Only Wire3 and the named competitors; Wire3 must be researched; plan fits the 40-search budget |
| **Research Agent** | Executes the plan through the MCP tools | `EvidenceSet` (records with URL, snippet, research question, timestamp) | Planned calls enforced in code (CrewAI); one company at a time with pauses (n8n); retries with backoff |
| **Analyst Agent** | Competitor and feature tables, pricing matrix (promo vs post-promo), themes, SWOT, 7P, unknowns and conflicts | `AnalystArtifact` | Every claim cites existing evidence ids; all 8 research questions covered; no invented Wire3 prices |
| **Strategy Agent** | ICPs, pains, value proposition (addressing the no-bundle and brand gaps), message pillars, local-budget channels, launch phases, metrics, risks | `StrategyArtifact` | Same citation checks; uncited claims must be labelled inference |

A fifth step, the **Docs Writer**, renders the two artifacts into a 19-section Google Doc, then reads it back
and verifies it. It is a renderer, not an agent, and it only receives validated content.

Prompts and role definitions: n8n's are in the agent nodes of `n8n/workflows/wire3_gtm_pipeline.json`;
CrewAI's are in `crewai/wire3_gtm/config/agents.yaml` and `tasks.yaml`. Both use **gpt-5-mini**.

## 4. MCP tools and search provider

`mcp-server/` is one MCP server that **both** implementations call, so search is identical by design.
Setup, tool arguments, result shape and cache details: [`mcp-server/README.md`](mcp-server/README.md).

| Tool | Purpose |
|---|---|
| `company_overview(company_name)` | Company background |
| `competitor_discovery(company_name, region)` | Competitors in a region |
| `product_portfolio_mapping(company_name)` | Plans, speeds, products |
| `pricing_research(company_name, region)` | Prices, promos, fees |
| `recent_news(company_name)` | News and announcements |
| `validate_source(url)` | HEAD request returning the status code; the caller classifies it as `ok`, `broken` (404/410/DNS), `blocked` (403/429) or `unverified` (timeouts, 5xx) (`crewai/wire3_gtm/links.py`) |

**Search provider: Tavily or SerpAPI, behind one adapter** (`mcp-server/research/providers/`,
selected by `SEARCH_PROVIDER`; it falls back to `tavily` if unset and raises on an unknown name, never
silently switching providers). Both are real, tested implementations.

- **Why both:** the capstone brief names SerpAPI, but SerpAPI's free tier is about 100 searches, and one run
  may use up to 40. Tavily's free tier (about 1,000 per month) paid for development and for **all measured
  runs, which used Tavily**. SerpAPI is implemented and unit-tested behind the same interface.
  (SETUP_DECISIONS.md, "Search provider".)
- **Which provider a run used** is recorded in its run record.

## 5. Environment and credential setup

**Prerequisites:** Python 3.12 + [uv](https://docs.astral.sh/uv/), Node.js 20+ (tested on 26), an OpenAI
API key, a Tavily or SerpAPI key, and for Docs output a Google Cloud project.

```bash
git clone <repo> && cd multi-agent-capstone-project
cp mcp-server/.env.example mcp-server/.env   # SEARCH_PROVIDER + TAVILY_API_KEY / SERPAPI_API_KEY
cp crewai/.env.example     crewai/.env       # OPENAI_API_KEY
cp n8n/.env.example        n8n/.env          # OPENAI_API_KEY
(cd mcp-server && uv sync) && (cd crewai && uv sync) && (cd n8n && npm ci)
```

**Where each secret lives** (none of them in git):

| Secret | Location |
|---|---|
| OpenAI key | `crewai/.env`, `n8n/.env` (n8n gets it via `CREDENTIALS_OVERWRITE_DATA` in `start.sh`, never typed into the UI) |
| Search keys | `mcp-server/.env` only |
| Google OAuth (n8n) | n8n's encrypted credential store in `n8n/.n8n/` (gitignored) |
| Google OAuth (CrewAI) | `crewai/.google/client_secret.json` + `token.json` (gitignored) |

**Google Cloud (once):** create a project, enable **Google Docs API** and **Google Drive API**, and configure
the consent screen (External, Testing, add yourself as a test user). Then create the two OAuth clients
described in [§8](#8-google-docs-and-pdf-export).

**Start the MCP server** (both implementations need it running):

```bash
cd mcp-server && uv run python main.py        # http://127.0.0.1:8000/mcp
uv run python health_check.py                 # optional: calls every tool once for real (uses ~6 search credits)
```

## 6. How to run the n8n workflow

```bash
cd n8n
./start.sh 2>&1 | tee logs/n8n_server.log     # loads .env, starts n8n on http://localhost:5678
```

First time only:
1. Open http://localhost:5678, create the owner account, and **import** `workflows/wire3_gtm_pipeline.json`
   (menu → Import from file, or `npx n8n import:workflow --input=workflows/wire3_gtm_pipeline.json` with n8n
   stopped).
2. Open the **OpenAI account** credential (the key is filled in from `.env`) and the **Google Docs account**
   credential ([§8](#8-google-docs-and-pdf-export)). Select them in the nodes that show a warning.
3. **Publish/activate** the workflow. An import unpublishes it.

Each run:

```bash
node scripts/run_pipeline.js            # POSTs the repo-root brief.json to the production webhook; waits ~7-9 min
node scripts/run_usage.js               # appends usage_summary + run_record (tokens, cost) to logs/runs.jsonl
node scripts/export_run.js <execution>  # optional: export an execution for analysis (eval/kpi/data/)
```

The response carries `document_url`. Use `--test` to hit the test webhook after clicking *Execute workflow* in
the editor. Full node-by-node walkthrough: [`n8n/RELIABILITY_AND_VALIDATION.md`](n8n/RELIABILITY_AND_VALIDATION.md).

## 7. How to run the CrewAI UV project

```bash
cd crewai
uv run python -m wire3_gtm run --docs google    # full run → Google Doc + PDF (~6-9 min)
uv run python -m wire3_gtm run --docs local     # same, document as local Markdown (no Google needed)
uv run python -m wire3_gtm run RUN_ID           # resume a failed/stopped run from its saved steps
uv run python -m wire3_gtm compare              # latest n8n run vs latest CrewAI run, side by side
uv run python -m wire3_gtm kpi --recheck-links  # recompute every KPI (eval/kpi/results.json)
```

Project layout is standard uv (`pyproject.toml`, `uv.lock`, `.python-version`). The pipeline is a
`crewai.Flow` (`wire3_gtm/flow.py`) over typed state. Each run's artifacts are saved to `crewai/runs/<run_id>/`
(`01_plan.json` … `06_link_check.json`, plus every rejected draft in `attempts/`). More detail:
[`crewai/README.md`](crewai/README.md).

## 8. Google Docs and PDF export

Both implementations create a **new private Doc** in the authorizing account's Drive, write it in one
`documents.batchUpdate` (headings, bullet tables, hyperlinked sources), then **read it back and verify it**:
headings, every cited source id resolves, no orphaned ids.

**n8n:**
1. In Google Cloud, create an OAuth client of type **Web application** with redirect URI
   `http://localhost:5678/rest/oauth2-credential/callback`.
2. In n8n, open the *Google Docs account* credential (Google Docs OAuth2 API), paste the client id and secret,
   and click **Sign in with Google**.
3. The workflow calls the Docs REST API through 3 HTTP Request nodes ("Build Docs Content" → create →
   batchUpdate → "Verify Document & Log").

**CrewAI:**
1. Create an OAuth client of type **Desktop app** in the same project and save its JSON as
   `crewai/.google/client_secret.json`.
2. `uv run python -m wire3_gtm google-auth` (opens a browser; scopes `documents` + `drive.file`).
3. `run --docs google` writes the Doc and exports the PDF through the Drive API to `runs/<id>/05_document.pdf`.

**PDF for n8n Docs:** File → Download → PDF in Google Docs.

**Samples:** [`samples/`](samples/) has:
- CrewAI `run-20260921-121832`: Markdown, **PDF**, and a verification record (19 headings, 61 sources cited).
- CrewAI `run-20260924-140309`: **PDF**, exported by the pipeline through the Drive API. This is the run in
  [`screenshots/crewai/`](screenshots/crewai/) (9.6 min, $0.17 LLM cost, 17/17 tool calls, Doc verified).
- n8n `client-muecjos5-gzgudf` (execution 38): **PDF**, downloaded from the Doc via File → Download → PDF.

n8n documents as read back after writing are in `eval/kpi/data/n8n_*.md`. The Google Docs themselves are
private to the developer's Drive, so the PDFs are the shareable copies.

> Refresh tokens expire after **7 days** while the consent screen is in Testing. Re-authorize before a run.

## 9. Logging, retry, budget and caching decisions

The full reasoning, with the cost of each decision if it's wrong, is in [`SETUP_DECISIONS.md`](SETUP_DECISIONS.md).

| Concern | n8n | CrewAI |
|---|---|---|
| **Logging** | Code nodes append JSON Lines to `n8n/logs/runs.jsonl` | `crewai/logs/runs.jsonl`, same event schema |
| **Run record** | `pipeline_start`, `agent_start/end`, `tool_call`, `validation_gate`, `document_write`, `run_complete`, `usage_summary`; schema in `schemas/run_record.schema.json` | same |
| **Tokens and cost** | Post-run script; character estimates, **lower bound** (no reasoning tokens) | Provider-reported per call, reasoning included |
| **Retries** | Agent nodes `retryOnFail`; model nodes `maxRetries` 6; retry count approximated from 429s in the server log | Search: 3 attempts, 2 s / 4 s backoff; failed check → re-prompt up to 3×; OpenAI SDK retries; all counted by kind |
| **Never retried** | Docs `batchUpdate` (a retry could write the text twice) | same; plus the doc id is saved so a rerun verifies instead of re-creating |
| **Budget** | Guard after each agent step stops on wall clock / search count with `BUDGET:`; cost checked after the run | Search calls refused at 40; time and cost checked between steps, after each rejected draft and before each search; breach → `stopped_budget`, artifacts kept, resumable |
| **Per-reply cap** | `maxTokens` 32k | `max_completion_tokens` 32k |
| **Caching** | Shared MCP cache | Shared MCP cache, `from_cache` logged per call |

**Caching** lives in the MCP server (`mcp-server/research/cache.py`), keyed by provider, query and
max_results, with a 24-hour TTL (`RESEARCH_CACHE_TTL_HOURS`, 0 = off). A hit returns the original
`retrieval_timestamp`, so evidence still shows when the page was actually read. Errors and empty results are
never cached.

**Budget stops were tested live:** CrewAI `run-20260922-225640` (cost cap $0.02) and n8n execution 31 (wall
clock 1 min) both stopped as `stopped_budget` with their partial artifacts kept.

**Interpretation note:** the brief's "6 LLM calls per role" is applied to reasoning calls. The Research Agent
makes one LLM step per tool call, so it is bounded by the plan (≤ 40 searches) instead. Both implementations
report the actual count. (SETUP_DECISIONS.md.)

## 10. Unit and scenario tests

All of these run **offline**: no API keys, no network, no search credits, no MCP server.

```bash
(cd crewai && uv run python -m pytest tests -q)      # 361 passed (~5 s)
(cd mcp-server && uv run python -m pytest tests -q)  # 77 passed
cd n8n
node scripts/test_research_gate.js                    # plan / research gate
node scripts/test_evidence_gate.js                    # evidence validation gate
node scripts/test_budget_guard.js                     # budget guard
node docs_writer/test_local.js docs_writer/fixtures/exec29   # Docs Writer build + verify against a mock Docs API
```

Last run 2026-09-23: **all pass.**

**What they cover:** contracts and schema parity (`test_wire_models`, `test_analyst_models`), citation and
coverage checks (`test_analyst_checks`, `test_strategy`), budget enforcement (`test_plan_enforcement`,
`test_boundaries`), link classification (`test_links`), the Docs field map and rendering (`test_docs_writer`,
`test_docs_field_map`), KPI counting rules (`test_kpi`), and the cache and providers (`mcp-server/tests`).
n8n's scripts extract the Code-node source from the workflow JSON, so they test the exported workflow itself.

**Scenario tests:**
- **Golden scenario:** `crewai/tests/test_golden_scenario.py` replays a full saved run (fixture in
  `tests/fixtures/golden/`) through every check.
- **Snapshot:** `tests/test_snapshot.py` rebuilds and verifies the whole document from `snapshots/`, with
  sha256 checks on every file.
- **Failure scenarios:** invented evidence id, self-referencing citation, tampered document, unresolvable
  id, budget stop. Each is in the tests above and was also observed live (see §14).
- **Human review:** blind rubric review of 6 documents (`eval/rubric.md`, `eval/reviews/kpi4/`), and an A/B
  test of an Analyst prompt variant (`eval/ab/`).

## 11. KPI results

Full method, per-run data and limitations: [`eval/KPI_REPORT.md`](eval/KPI_REPORT.md). Recompute with
`uv run python -m wire3_gtm kpi --recheck-links`. Counted runs: CrewAI ×3, n8n ×3 completed of 4, all on
current code (2026-09-23).

| KPI | Target | CrewAI | n8n |
|---|---|---|---|
| 1. Research coverage | ≥ 90% | **100%** ✅ | **100%** ✅ |
| 2a. Top-tier sources | ≥ 80% | 58–69% ❌ | 68–73% ❌ |
| 2b. Broken links | 0% | **0%** ✅ | **0%** ✅ |
| 3. Latency | < 15 min | **6.0–9.3 min** ✅ | **7.2–8.8 min** ✅ |
| 4. Strategy quality | ≥ 4 / 5 | **4.83** ✅ (provisional) | **4.61** ✅ (provisional) |
| 5. Reproducibility | ≥ 80% | **92%** ✅ | 78% ❌ (≈90% with wording normalized) |
| 6. Cost per run | ≤ $2.50 | **$0.12–0.18** ✅ | **≥ $0.10–0.11** ✅ (lower bound) |
| Reliability | | 3 / 3 completed | 3 / 4 completed |

**Reading it:** both meet coverage, links, latency and cost comfortably. Neither reaches 80% top-tier sources.
The A/B test showed that limit comes from what the searches return, not from the Analyst's choices. KPI 4 is
provisional: one reviewer (the author), with evidence lines still to be added.

## 12. n8n versus CrewAI

Full table with a "don't write" line per row: [`eval/COMPARISON_TABLE.md`](eval/COMPARISON_TABLE.md).
Every change made for parity: [`eval/COMPARISON_LOG.md`](eval/COMPARISON_LOG.md).

| Dimension | CrewAI | n8n | Edge |
|---|---|---|---|
| Agent hand-off | 0, 1, 0 invented ids per run; the model fixes its own draft | 15, 2, 17 invented ids **deleted** per run; 1 run lost to a bad citation | CrewAI |
| Error handling | 3/3 completed; failed checks re-prompt the model; runs resume from disk | 3/4 completed; a check after the LLM node can only repair or stop | CrewAI |
| MCP integration | 182-line wrapper (retries, budget, evidence) | 1 node to connect; then a 4-node per-company loop after rate-limit and context-window failures | CrewAI |
| Reproducibility | 92% | 78% (free-text service type) | CrewAI, as measured |
| Source quality | 58–69% top-tier | 68–73% top-tier | n8n, slightly |
| Latency | 7.6 min avg | 7.9 min avg | none meaningful |
| Citation coverage | 100% RQs; 79% of claims sourced | 100% RQs; 77% of claims sourced | none meaningful |
| Search integration | shared MCP server | shared MCP server | identical |
| Cost | $0.148 avg, exact | ≥ $0.108 avg, lower bound | not comparable |
| Ease of modification | edit file + 361 tests in ~5 s | edit JSON (+ copies) → CLI import → restart → re-publish | CrewAI for this project |
| Setup, clarity, debugging | 1 calendar day to a verified Doc | 2 calendar days; the whole pipeline on one canvas | not measured (hours not recorded; CrewAI built second) |

**Conclusion.** The real differences come from the orchestrators themselves. CrewAI's guardrails run
*inside* a task and can re-prompt the model. n8n validates *after* the LLM node, so it can only delete a bad
citation or stop the run. That one mechanism explains most of the reliability and hand-off gap. n8n's agent
node keeps every tool result in context, which forced the per-company research loop. n8n was fastest to wire
up and is the easiest to see at a glance. CrewAI was cheaper to change and test at this plan size.
**Bias to note:** both were built largely with an AI coding assistant, which favours code-first tools.

## 13. Known limitations and future improvements

- **Top-tier sources below 80%** in both. Next step: searches aimed at official sites (e.g. `site:` queries for
  provider pricing pages and the FCC map).
- **KPI 4 is provisional:** single reviewer who is also the author; evidence lines pending; scores near the
  ceiling. Needs a second reviewer.
- **Small samples:** 3 counted runs per implementation.
- **n8n cost is a lower bound** (no reasoning tokens) and neither figure includes search fees. Confirm with the
  OpenAI usage dashboard.
- **n8n has no run-time link check** and free-text service types (fix: an enum in its Analyst schema, as CrewAI has).
- **Wire3 prices are not public**, so every plan lists them as unknown, and no plan can give a firm price
  recommendation. That is by design, under the academic boundary.
- **Research is exempt from the literal "6 LLM calls per role"** cap (see §9).
- **Google Docs tables are rendered as bullet lists**, not native tables.
- **Google OAuth is Testing-mode, local only:** tokens expire in 7 days; Docs are private until shared.
- **Tier classification is judgment** (`eval/kpi/source_tiers.yaml`), and it measures the kind of source, not
  whether it is relevant.

## 14. Error resolutions and troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| n8n Research: 429, 500k tokens-per-minute limit (exec 32) | The agent resends its whole tool-result history on each call; retries re-accumulate the same load | Research one company at a time with 15 s pauses (COMPARISON_LOG #7, #9) |
| n8n Research: context window exceeded (exec 34, 337k tokens) | All planned calls in one agent trajectory | Same per-company loop (#8) |
| n8n run failed on citation `CHANNEL-1` (exec 36) | Strategy cited its own label as a source | Check rejects it; documented as an orchestrator finding (#11) |
| Analyst invents evidence ids | LLM hallucination | CrewAI: guardrail re-prompts; n8n: gate removes them and records the count (#2, #10) |
| CrewAI: a validator error raised inside the OpenAI SDK and lost a 9-min step | `chat.completions.parse` runs Pydantic validators | Validator-free "wire" models for the LLM call; strict check in the guardrail |
| CrewAI Research stopped after 6 of 15 calls | `max_iter=6` counts each tool step | `max_iter` 50 for Research; plan enforced in code |
| Analyst rejected on coverage in 12/12 first drafts | Coverage demanded the copy of the result tagged with that question's id | Coverage judged per search result; the Analyst step fell from 511 s to 205 s on average |
| Every n8n evidence URL marked invalid | `new URL()` isn't available in the Code-node sandbox | Regex URL check |
| Healthy links reported broken | Timeouts counted as broken | Only 404/410/DNS count as broken; 403/429 = blocked, timeouts = unverified |
| n8n Research stopped at 10 iterations | Agent node default `maxIterations` | Raised to 50 |
| `redirect_uri_mismatch` on Google sign-in | Redirect URI not yet applied | Use exactly `http://localhost:5678/rest/oauth2-credential/callback`; wait a few minutes |
| Google auth error a week after setup | Testing-mode refresh tokens expire after 7 days | n8n: *Sign in with Google* again; CrewAI: `google-auth` |
| Workflow stopped responding to the webhook after import | CLI import unpublishes the workflow | Re-publish it in the editor |
| CrewAI: "MCP server unreachable" | Server not started | `cd mcp-server && uv run python main.py` |
| Code node can't read `process.env` | n8n blocks env access in Code nodes | Log path is a relative literal; `NODE_FUNCTION_ALLOW_BUILTIN=fs` in `start.sh` |

More: `n8n/RELIABILITY_AND_VALIDATION.md` ("Other issues found and fixed"), `eval/COMPARISON_LOG.md` #1–#12,
and `SETUP_DECISIONS.md`.
