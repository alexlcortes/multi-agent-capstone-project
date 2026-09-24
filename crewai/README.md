# Wire3 GTM pipeline: CrewAI implementation

Brief → **Head Planner** → **Research** → **Analyst** → **Strategy** → Google Doc. The same brief and the same
artifact contracts (`../schemas/`) as the n8n implementation in `../n8n/`, so the two can be compared.

The pipeline is a `crewai.Flow` (`wire3_gtm/flow.py`). Each step hands the next a validated Pydantic object held in
the Flow's typed state, and every step is also saved to disk as it finishes:

| Step | Produces | Contract |
|---|---|---|
| Head Planner | bounded research plan | `ResearchPlan` |
| Research (MCP tools) | evidence records, built in code from raw tool output | `EvidenceSet` |
| Analyst | comparison tables, pricing, themes, SWOT, 7P, unknowns | `AnalystArtifact` |
| Strategy | ICPs, positioning, channels, phases, metrics, risks | `StrategyArtifact` |
| Docs Writer | Google Doc + PDF (or local Markdown) | rendered from the two artifacts above |

## Set up

Needs [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
cd crewai
uv sync                      # installs from uv.lock
cp .env.example .env         # then put your OPENAI_API_KEY in it
uv run python -m pytest tests -q     # 80+ offline tests; no keys, network or MCP server needed
```

**Start the MCP research server first** (a separate process; the pipeline checks it is reachable and fails
fast if not):

```bash
cd ../mcp-server
cp .env.example .env         # set SEARCH_PROVIDER (tavily or serpapi) and its API key
uv sync && uv run python main.py     # serves http://127.0.0.1:8000/mcp
```

## Run

```bash
uv run python -m wire3_gtm run                  # Planner → Research → Analyst → Strategy
uv run python -m wire3_gtm run --docs local     # ...plus the document as Markdown (no Google needed)
uv run python -m wire3_gtm run --docs google    # ...plus a real Google Doc and PDF (see Google setup)
uv run python -m wire3_gtm run RUN_ID           # resume: finished steps are loaded from disk, not re-run
uv run python -m wire3_gtm compare              # latest n8n run vs latest CrewAI run, side by side
uv run python -m wire3_gtm snapshot RUN_ID      # re-validate a run's Strategy output, save to ../snapshots/RUN_ID
uv run python -m wire3_gtm executive-brief RUN_ID   # short CEO version of a finished run's plan (local Markdown, no LLM)
```

`runs/` is gitignored. A snapshot is the committed copy of one validated Strategy output: its inputs (plan, evidence, Analyst artifact), `strategy.md`, and `snapshot.json` with the checks passed and a sha256 per file. `wire3_gtm.snapshot.load_snapshot()` refuses edited files, and `tests/test_snapshot.py` builds and verifies the full document from every committed snapshot, with no Google connection.

A run takes roughly 8–12 minutes and, at the pricing in `wire3_gtm/run_log.py`, costs well under the $2.50 budget
in LLM tokens (search-provider fees are not included). The brief is the repo-root `../brief.json`, shared with n8n.

## What a run leaves behind

`runs/<run_id>/` (gitignored): `01_plan.json`, `02_tool_calls.jsonl` (one line per search, written as each returns),
`02_evidence_set.json`, `03_analyst_artifact.json`, `04_strategy_artifact.json`, `05_document.md/.json/.pdf`, `05_executive_brief.md` (only after `executive-brief RUN_ID`),
`06_link_check.json`, `06_run_summary.txt`, `manifest.json`, and `attempts/` (every guardrail attempt, including
rejected drafts). If a step fails, everything before it is kept: rerun with the run id to resume, or
`uv run python -m wire3_gtm salvage RUN_ID` to recover a failed Analyst step from its saved drafts.

`logs/runs.jsonl` is the run record, in the same event schema as `../n8n/logs/runs.jsonl`
(`pipeline_start`, `agent_start/end`, `tool_call`, `validation_gate`, `document_write`, `run_complete`,
`usage_summary`). It carries latency per agent, provider-reported token usage, cost, retries by kind, search
calls, broken links and document status.

## Reliability and budget

* **Retries:** search tool calls retry transient failures 3 times with 2 s / 4 s backoff (every attempt counts
  against the search budget, and only the final result becomes evidence, so a retry cannot duplicate it). A step
  that fails validation is re-prompted up to 3 times, each attempt saved and logged. The Google `batchUpdate` is
  never retried, because a retry could insert the document twice.
* **Budget** (from the brief): 12 min, 40 search calls, $2.50. Search calls are refused once the limit is used;
  time and cost are checked between steps and after every rejected draft, and a breach stops the run with status
  `stopped_budget` and its artifacts kept.
* **Cost:** from provider-reported tokens including hidden reasoning tokens, so the LLM figure is exact for the
  model priced in `run_log.py` (prices copied from the n8n implementation, last verified 2026-09-19; update
  both when they change). Search-provider fees are excluded.

## Google Docs setup (only for `--docs google`)

1. In your Google Cloud project, enable the Google Docs API and Google Drive API.
2. Create an OAuth client of type **Desktop app**; download its JSON to `crewai/.google/client_secret.json`
   (gitignored).
3. `uv run python -m wire3_gtm google-auth` once (opens a browser, asks for the Docs and `drive.file` scopes).
   While the consent screen is in *Testing* mode the token expires after 7 days; rerun this if a run reports an
   auth error.

The Doc is created private to that Google account. Each run creates a new Doc, so test runs need occasional
manual deletion in Drive.

## Design notes

Decisions and their trade-offs are in `../SETUP_DECISIONS.md` (LLM-call budget interpretation, credentials,
Docs Writer). Known limits: tables are rendered as bullet lists, not Google Docs tables; source quality depends
on what the search provider returns and is reported, not forced, in each run.
