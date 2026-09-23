# n8n vs CrewAI: comparison table

Same brief (`brief-d516e1daddfb`), same model (gpt-5-mini), same MCP research server and search provider (Tavily),
same document template. Run figures are from the counted runs in `eval/kpi/runs.yaml`: CrewAI runs 2026-09-23
130640, 131659 and 132404; n8n executions 35, 37 and 38 (plus execution 36, which failed). Numbered references
(#4) point to `eval/COMPARISON_LOG.md`.

**Rule for every row:** a cell states a measurement, a count, or a specific example with its source. Where
nothing was measured, the cell says so. "Easier", "cleaner" or "more intuitive" without a number or an example
behind it does not go in this table; each row's *Don't write* line names the tempting version.

## Summary

| Dimension | CrewAI | n8n | Difference | Basis |
|---|---|---|---|---|
| Setup effort | 1 calendar day from first commit to verified Doc (2026-09-20); 3 commits | 2 calendar days (2026-09-18/19); 4 commits | Not comparable as measured | Git history; hours not recorded |
| Workflow clarity | 31 Python files, 5,583 lines; 4 roles in 282 lines of YAML | 31 nodes, 31 connections; 1,447 lines of JS in 13 Code nodes | No measurement of clarity itself | Code and workflow counts |
| Agent hand-off | 0, 1, 0 invented ids per run; the Analyst re-prompted in 2 of 3 runs | 15, 2, 17 invented ids removed per run; 1 of 4 runs lost to one bad id | **CrewAI** | Run records; #10, #11 |
| MCP integration effort | 182-line module (adapter, retries, recording) | 1 MCP node, then a 4-node batching loop after two failed runs | **CrewAI** | #7–#9; executions 32, 34 |
| Search integration effort | Shared MCP server | Shared MCP server | **None**: identical | `mcp-server/` |
| Docs integration effort | 677 lines (content + Google client) + own OAuth client | 404 lines of JS + 3 HTTP Request nodes; OAuth in n8n's credential form | Different kinds of work; hours not recorded | File sizes; SETUP_DECISIONS.md |
| Average latency | 7.6 min (6.0–9.3) | 7.9 min (7.2–8.8) | **None meaningful** | KPI 3 |
| Cost per run | $0.148 average ($0.12–0.18), provider-reported | ≥ $0.108 average, a lower bound | **Not comparable** (n8n misses reasoning tokens) | KPI 6 |
| Citation coverage | 100% of research questions; 79% of strategy claims sourced | 100% of research questions; 77% of strategy claims sourced | **None meaningful** | KPI 1; artifact basis counts |
| Source quality | 58–69% top-tier; 0 broken links | 68–73% top-tier; 0 broken links | n8n slightly higher; neither meets 80% | KPI 2 |
| Reproducibility | 92% | 78% (≈90% with wording normalized) | **CrewAI**, as measured | KPI 5; #12 |
| Error handling | 3 of 3 runs completed; failed checks re-prompt the model | 3 of 4 completed; checks can only repair or stop | **CrewAI** | Run records; #4, #10, #11 |
| Debugging experience | Every step's output and every rejected draft saved per run | Execution data in n8n's database and editor; needed an export script for analysis | Examples only; time to diagnose not measured | See row notes |
| Ease of modification | Change = edit file + 361 tests in ~4 s | Change = edit JSON (+ copies) + CLI import + restart + re-publish | **CrewAI** for this project | Today's changes (#4–#9) |

**Where they genuinely differ:** hand-off and error handling, MCP integration under load, and the cost of
changing the workflow. **Where they don't:** latency, citation coverage, search integration. **Where the
data can't decide:** setup effort, clarity, debugging, and cost.

---

## Row notes

### Setup effort
- **Measured:** commit history. n8n went from its first node ("Add n8n workflow: trigger, Head Planner, Research
  Agent…", 2026-09-18) to a verified Google Doc ("Replace Docs Writer placeholder with real Google Docs writer",
  2026-09-19): 2 calendar days, 4 commits. CrewAI went from "Add CrewAI pipeline…" to "Add CrewAI Docs Writer…"
  on the same day, 2026-09-20, in 3 commits.
- **Why it can't decide:** hours were not recorded. CrewAI was built second, reusing the schemas, the MCP
  server and lessons from n8n. Both were built mostly by an AI assistant, which favours code-first tools.
- **Don't write:** "CrewAI was faster to set up." Write: "CrewAI reached a verified document in 1 calendar day,
  n8n in 2; hours weren't recorded, and CrewAI was built second."

### Workflow clarity
- **Measured (proxies only):** n8n: 31 nodes, 31 connections, 13 Code nodes holding 1,447 lines of JavaScript,
  3 prompt nodes, 3 output schemas. CrewAI: 31 Python files, 5,583 lines, plus 282 lines of YAML for the 4
  roles and their tasks. CrewAI's total includes its logging, KPI, A/B and review tooling; n8n's equivalents
  are separate scripts.
- **One concrete point:** in n8n the pipeline order is visible on one canvas. In CrewAI it is `flow.py` plus
  the task order in `tasks.yaml`.
- **Don't write:** "n8n is more visual, so clearer." Clarity for *whom* and *measured how*? A real test would be:
  give a newcomer each implementation and time how long they take to answer "where is the Wire3 plan rule
  enforced?" Not done.

### Agent hand-off behaviour
- **Measured:** the checks between agents.
  - **CrewAI:** invented ids per run 0, 1, 0. The Analyst's check rejected a first draft in 2 of 3 runs, and
    the model then fixed it itself (guardrail retries: 1, 0, 1).
  - **n8n:** invented ids removed per run 15, 2, 17 (execution 35, 37, 38). The model is never asked to fix
    them: the ids are deleted and the claim loses citations. Execution 36 failed outright because the Strategy
    step cited its own label, `CHANNEL-1` (#11).
- **Why:** CrewAI's guardrails run inside the task and re-prompt. n8n's validation runs in a Code node after
  the chain node, which can't re-prompt.
- **Don't write:** "CrewAI's hand-offs are more robust." Write the counts above.

### MCP integration effort
- **CrewAI:** `research_tools.py` (182 lines) connects through `MCPServerAdapter` and wraps each tool with
  retries, budget checks and evidence recording. Search results are collected in code, and the Research Agent
  made 2 model calls per run.
- **n8n:** one MCP Client Tool node connected the tools in minutes. But the agent keeps every tool result in
  the model's input. Execution 32 hit the 500k tokens-per-minute limit (17 model calls, 752k input tokens);
  execution 34 exceeded the context window (337k). The fix was 4 more nodes (split by company, loop, pause,
  collect; #9). The evidence builder also has to unwrap n8n's JSON-in-JSON tool output (246-line Code node).
- **Don't write:** "MCP was easy in n8n." It was quick to *connect* and expensive to make *work at this plan
  size*. Say both.

### Search integration effort
- **Identical by design:** both call the same 5 MCP tools on the same server, with the same provider adapter
  (SerpAPI/Tavily) and the same 24-hour cache (`mcp-server/research/`).
- **Don't write** any difference here. If one is wanted, it belongs under MCP integration.

### Docs integration effort
- **CrewAI:** `docs_content.py` (430 lines) and `docs_google.py` (247 lines), using Google's Python client,
  with its own OAuth desktop client and a `google-auth` command (SETUP_DECISIONS.md, "CrewAI Docs Writer").
- **n8n:** "Build Docs Content" (324 lines of JS) and "Verify Document & Log" (80), plus 3 HTTP Request nodes.
  The Google OAuth client was entered in n8n's credential form, which handles token refresh. That secret
  lives in n8n's database, not in `.env` (SETUP_DECISIONS.md).
- **Parity cost:** n8n's renderer lived in 3 copies (the JS file, the workflow JSON, `new_nodes.json`) and
  lagged CrewAI's layout until 2026-09-23 (#6).
- **Don't write:** "Google Docs was simpler in n8n." OAuth was simpler, the rendering code was the same work,
  and keeping 3 copies in step is extra.

### Average latency
- **Measured (KPI 3):** CrewAI 9.3, 6.0, 7.6 min (average 7.6); n8n 7.2, 8.8, 7.6 min (average 7.9). Both are
  within the 12-minute brief budget and the 15-minute KPI.
- **Don't write:** "n8n is slower." A 0.3-minute gap over 3 runs each is well inside the run-to-run spread
  (3.3 minutes for CrewAI alone). n8n's figure includes about 45 seconds of deliberate pauses.

### Cost per run
- **Measured (KPI 6):** CrewAI $0.182, $0.120, $0.141 (average $0.148), from provider-reported tokens. n8n
  ≥ $0.100, ≥ $0.113, ≥ $0.112 (average ≥ $0.108), a lower bound because n8n can't see reasoning tokens.
- **Don't write:** "n8n is cheaper." The numbers aren't measured the same way. To compare, take both from the
  OpenAI usage dashboard for the same run windows.

### Citation coverage
- **Measured:** all 8 research questions cited in every run of both (KPI 1). Strategy claims with a source:
  CrewAI 85%, 78%, 73% (average 79%); n8n 74%, 77%, 81% (average 77%); the rest are marked inference.
- **Don't write:** "CrewAI cites more." 79% against 77% over 3 runs each is noise.

### Source quality
- **Measured (KPI 2):** top-tier share of distinct cited sources: CrewAI 58%, 65%, 69%; n8n 73%, 72%, 68%. Broken
  links: 0 in every document of both.
- **Don't write:** "n8n finds better sources." The difference is small, the tiers are a rule-based judgment,
  and it measures the kind of source, not relevance. Neither meets the 80% target, and the A/B test showed
  the limit is what the searches return.

### Reproducibility
- **Measured (KPI 5):** agreement on 17 decision items across 3 runs: CrewAI 92%, n8n 78%. Most of n8n's
  disagreement is free-text wording of service type ("hybrid (fiber & HFC)" against "hybrid (HFC/fiber)").
  With the wording mapped, it's about 90%. Both are least consistent on competitor pricing.
- **Don't write:** "CrewAI is more deterministic." Write: "CrewAI 92% against n8n 78%, mostly because CrewAI's
  schema fixes the allowed values (#12); both vary most on pricing."

### Error handling
- **Measured:** completed runs on the counted versions: CrewAI 3 of 3, n8n 3 of 4. Failures during the day's
  n8n work: rate limit (execution 32), context window (execution 34), self-referencing citation (execution 36).
- **Mechanisms:** CrewAI re-prompts on a failed check (guardrails), retries transient tool failures with
  backoff, and can resume a failed run from its saved steps. n8n retries the agent node (`retryOnFail`), can
  only repair or stop after a bad LLM output, and reruns from the start.
- **Don't write:** "CrewAI handles errors better." Write the completion counts and the mechanism that caused
  each n8n failure.

### Debugging experience
- **Examples, not measurements:**
  - **CrewAI:** each run keeps every step's output (`01_plan.json` … `06_link_check.json`) and every rejected
    draft (`attempts/`). The A/B failure (#2) was diagnosed by opening `03_analyst_artifact.json` and
    finding the invented id in `reason_unresolved`.
  - **n8n:** the editor's execution view shows each node's input and output, which is quick for one run. But
    analysing runs outside the editor meant decoding n8n's database format. That needed a script
    (`n8n/scripts/export_run.js`), and the rate-limit cause (#7) was only visible in the stored execution
    error.
- **Don't write:** "debugging is easier in CrewAI." Nobody timed a diagnosis. Keep the examples and say it
  wasn't measured.

### Ease of modification
- **Measured on 2026-09-23's changes:**
  - **CrewAI:** a change is a file edit, then 361 tests in about 4 seconds.
  - **n8n:** a change to a Code node or prompt means editing the workflow JSON (and the separate copy of
    shared code in `docs_writer/`), stopping n8n, importing with the CLI, restarting, and re-publishing in
    the editor, since an import unpublishes the workflow. That happened 3 times that day.
  - **Found only in paid runs:** 3 n8n problems (executions 32, 34, 36) against 2 for CrewAI (A/B runs A0 and
    B0 of the aborted first attempt, #2 and #3).
  - **Offline tests:** n8n 48 checks in 4 scripts, which extract code from the workflow JSON; CrewAI 272 test
    functions.
- **Bias:** an AI assistant made these changes, and it works well with code. A person editing in n8n's canvas
  might have a different experience.
- **Don't write:** "CrewAI is easier to change." Write the step counts and the test counts, and note who made
  the changes.
