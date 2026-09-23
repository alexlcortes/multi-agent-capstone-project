# Comparison log: n8n vs CrewAI

Every change made so that the two implementations can be compared fairly, and every difference found along
the way, with the evidence and the reason. Newest last. Design decisions that are not about the comparison
stay in `SETUP_DECISIONS.md`; the KPI results are in `eval/KPI_REPORT.md`.

Each entry answers: what differed, how we know, what changed (where, which commit), why, what it does to
earlier numbers, and whether it says something about **the orchestrators themselves** or only about **our
build** of them. Only the first kind belongs in the final "n8n vs CrewAI" conclusion.

**Legend.** *Parity*: we made the two do the same thing. *Reliability*: a run could fail or mislead.
*Kept*: a real difference, recorded and not removed. *Orchestrator finding*: the difference comes from how
n8n or CrewAI works, not from a choice we made.

## Summary

| # | Date | Change | Where | Type | Orchestrator finding? |
|---|---|---|---|---|---|
| 1 | 2026-09-23 | Inferred values marked `[inference; ids]` | both | Parity, reliability | No |
| 2 | 2026-09-23 | Invented evidence ids in free text caught | CrewAI | Reliability | No |
| 3 | 2026-09-23 | Pricing plan-name citations rendered | CrewAI (n8n in #6) | Reliability | No |
| 4 | 2026-09-23 | Planner: only Wire3 and named competitors; Wire3 must be researched; a plan check enforces it | both | Parity | Partly |
| 5 | 2026-09-23 | Repeated and unplanned research calls produce no evidence | n8n | Reliability | Partly |
| 6 | 2026-09-23 | n8n document uses CrewAI's 19-section layout | n8n | Parity | No |
| 7 | 2026-09-23 | Research rate limit: retries were not the fix | n8n | Reliability | **Yes** |
| 8 | 2026-09-23 | Research context window: all-at-once calls overflow | n8n | Reliability | **Yes** |
| 9 | 2026-09-23 | Research runs once per company, with a pause | n8n | Reliability | **Yes** |
| 10 | 2026-09-23 | n8n's Analyst invents evidence ids; its gate deletes them instead of re-prompting | n8n | Kept | **Yes** |
| 11 | 2026-09-23 | n8n's Strategy cited its own label as a source; the run stopped with no document | n8n | Kept | **Yes** |
| 12 | 2026-09-23 | n8n's service type is free text, so identical answers read as different | n8n | Kept (for now) | No |

**Runs before these changes do not compare like-for-like with runs after them.** `KPI_REPORT.md` counts only
n8n's final workflow (executions 35–38) and CrewAI's fresh runs on current code (2026-09-23 17:06 UTC on);
see `eval/kpi/runs.yaml`.

---

## 1. Inferred values were shown as if sourced

- **What differed / found:** a field with `basis: "inference"` that also listed the ids it was inferred from
  was rendered as `Yes [EV-d1118463]`, which reads like a sourced fact. 25 such fields in CrewAI's golden run.
  Both Docs Writers had the same logic.
- **Evidence:** golden scenario check `inference_marking_errors` (`crewai/tests/test_golden_scenario.py`).
- **Change:** both renderers now write `Yes [inference; EV-d1118463]`. Commit `f7bd587`.
- **Why:** the document's own legend says `[inference]` marks judgment; the rubric's citation criterion
  depends on telling sourced facts from judgment.
- **Effect on earlier numbers:** documents written before this read more confident than they were. Word
  counts grew by 27 in the golden document.
- **Orchestrator finding:** no, a shared rendering choice.

## 2. Invented evidence ids written into prose

- **Found:** in an A/B run, the CrewAI Analyst wrote two invented ids (`EV-123bb33f`, a near-copy of a real
  id, and `EV-4e556634`) inside an unknown's `reason_unresolved` sentence. The id fields were clean, so the
  grounding check passed, and the Docs Writer refused the document at the very end.
- **Evidence:** run `ab-analyst-sources-B0-20260923-131630` (aborted first A/B attempt).
- **Change:** the Analyst and Strategy grounding checks also scan free text, so the model is re-prompted
  instead of the run failing late. Commit `2c0fdd2`.
- **Why:** reliability: a late failure wastes the whole run.
- **Orchestrator finding:** no. n8n's evidence gate already removes invented ids (it removed 5 in execution
  33), by deleting them rather than re-prompting.

## 3. Pricing plan-name citations dropped

- **Found:** each pricing row used the plan name as its heading and skipped the field, so the plan name's
  citations never reached the document or Appendix B.
- **Evidence:** A/B run A0 of the first attempt failed two golden checks (checkthat.ai and
  broadbandsearch.net sources missing).
- **Change:** CrewAI renders a "Plan name: … [EV-…]" line in each row. Commit `2c0fdd2`. n8n got the same
  behaviour with its new layout (#6).
- **Orchestrator finding:** no.

## 4. Planner researched topics instead of companies, and never researched Wire3

- **What differed:** n8n's Head Planner put topics in `company_name` ("Regional fiber ISPs (case studies…)",
  "Marketing channels for regional ISPs") and planned **no** Wire3 calls. CrewAI's plan had 4 Wire3 calls.
- **Evidence:** n8n execution 29: 13 planned calls, none for Wire3; 0 of 400 evidence records from
  wire3.com; its Analyst filled Wire3's speeds with "placeholders" (1000/1000). Rubric first pass scored n8n
  2/5 on differentiation and evidence quality for this reason.
- **Why they differed:** CrewAI's planner prompt said "company_name must be Wire3 or one of the brief's
  competitors" and a guardrail enforced it; n8n's prompt had neither.
- **Change (commit `e82b313`):**
  - n8n: the same rule in the Head Planner prompt, plus "research Wire3 itself" and "region only for
    competitor_discovery and pricing_research"; a plan check in "Log: Head Planner" rejects a plan that
    breaks it.
  - CrewAI: its guardrail now also requires Wire3 research (before, only its prompt implied it).
- **Result:** every n8n plan since has passed the check (executions 32–35; execution 32's had 4 Wire3
  calls). Execution 33 found 61 wire3.com records, and Wire3's row is now cited (footprint confirmed, 10,000 Mbps).
- **Orchestrator finding: partly.** The missing rule was our omission. But the two handle a bad plan
  differently: CrewAI re-prompts the planner inside the task; n8n's chain node cannot, so a rejected plan
  stops the run (cheaply, within about a minute).

## 5. Research calls executed twice

- **What differed:** n8n's Research Agent ran all 13 planned calls twice and added a `validate_source` call
  it was not asked for. The repeats matched no planned call, so 130 evidence records had no research
  question.
- **Evidence:** execution 29 (27 executed / 13 planned); KPI 1 coverage for n8n was 50%, partly because of
  these records.
- **Change:** "Build Evidence Records" ignores repeated and unplanned calls (they are still logged as made
  and paid for). Commit `e82b313`. Test: `n8n/scripts/test_research_gate.js`.
- **Orchestrator finding: partly.** Both agents can drift from the plan. CrewAI enforces the plan in code
  (`enforce_plan` runs any planned call the agent skipped); in n8n the equivalent has to be added as a Code
  node after the agent.

## 6. n8n's document showed only the Strategy sections

- **What differed:** n8n rendered 10 Strategy sections; CrewAI rendered 19, including the executive summary,
  research scope, competitor comparison, pricing matrix, themes, SWOT and 7P. Known and documented as
  `schemas/gtm_document_template.md` rule 4, but not fixed.
- **Evidence:** execution 29 document: 1,752 words, 13 sources, no competitor or pricing data, though its
  Analyst artifact had them. Coverage KPI 50%; rubric clarity 2/5 and usefulness 2/5.
- **Change:** n8n's "Build Docs Content" uses CrewAI's section layout. Re-rendered from execution 29's data:
  19 sections, 46 sources. Commit `e82b313`. Test: `n8n/docs_writer/test_local.js` with a committed fixture.
- **Orchestrator finding:** no, an unfinished part of our n8n build.

## 7. Rate limit in the Research Agent, and why retries were not the fix

- **Found:** the first run after #4 failed in the Research Agent: *"Rate limit reached for gpt-5-mini … on
  tokens per min (TPM): Limit 500000, Used 467440, Requested 127284."*
- **Evidence:** execution 32: 19 planned calls; the agent made **17 model calls using 752,251 input tokens in
  about 45 seconds**.
- **Why:** n8n's agent node keeps every tool result in the model's input and re-sends it on every step, so
  input grows with each call (roughly with the square of the number of calls when it calls one at a time).
  #4 made plans larger (Wire3 added about 4 calls), which pushed it over the per-minute limit.
- **What did not work:** raising the OpenAI node's "Max Retries". It was already 6 on all four model nodes,
  and the run failed anyway. (An earlier suggestion here to raise it was wrong; noted so nobody retries it.)
- **Orchestrator finding: yes.** The same research in CrewAI used 2 model calls: its pipeline runs the
  planned searches in code, outside the model's input.

## 8. Context window in the Research Agent

- **Tried:** instructing the agent to make all planned calls at once, as parallel tool calls, so it would
  need few model calls. (Execution 33, which completed, happened to do this: 3 model calls, 275,665 tokens.)
- **Result:** execution 34 failed: *"400 Your input exceeds the context window of this model."* 6 model
  calls, 336,838 input tokens.
- **Why:** with about 16 calls x 10 results, all results together are close to the most gpt-5-mini accepts
  in one request. Calling one at a time hits the rate limit (#7); calling all at once overflows the input.
  Execution 33 fitted by luck.
- **Orchestrator finding: yes,** the same cause as #7.

## 9. Research runs once per company

- **Change:** "Split Plan by Company" turns the plan into one batch per company (Wire3 first); a "Research
  Loop" runs the Research Agent on each batch, with a 15-second "Pause Between Companies"; "Collect Research
  Steps" merges the steps back for "Build Evidence Records". Each agent run carries 4–5 calls, about
  60,000–75,000 tokens. The agent is still an agent and makes the same calls.
- **Tests:** `test_research_gate.js` checks the split, the merge, that per-company evidence equals one run of
  the same calls, the wiring, and that the pause is 15 seconds (the Wait node's default unit is hours).
- **Result:** execution 35 (`client-muebb850-kyrc43`) completed: 16 of 16 planned calls, 402 evidence records,
  the Research Agent used **8 model calls and 141,047 input tokens** (2 per company), against 752,251 in
  execution 32 and 336,838 in execution 34. Whole run 7.2 min, at least $0.10; verified document with 19
  sections and 38 sources.
- **Cost:** a slower research step (the pauses add about 45 seconds) and a longer workflow (4 more nodes).
- **Orchestrator finding: yes.** Keeping a research agent within model limits needed batching in n8n. In
  CrewAI the equivalent problem did not arise, because tool results are collected outside the model's input.

## 10. n8n's Analyst invents evidence ids, and n8n can only delete them

- **Found:** both completed runs of the fixed workflow finished "degraded" because their Analyst cited
  evidence ids that do not exist: 5 in execution 33, 15 in execution 35 (e.g. `EV-157b1b2`, one character
  short of a real id). n8n's "Validate Evidence Coverage" removed them, so the documents are clean.
- **Why it matters:** each removal leaves the claim with fewer citations, or downgrades it to inference. CrewAI
  catches the same mistake in its guardrail and re-prompts the Analyst to fix it; n8n's chain node cannot
  re-prompt, so it can only delete (or stop the run).
- **Change:** none: recorded as a real difference. The count is worth tracking per run
  (`issues` in each run record).
- **Orchestrator finding: yes,** the same limitation as #4: validation after the LLM step, not inside it.

## 11. A self-referencing citation stopped an n8n run

- **Found:** execution 36 (`client-muebmst4-z4npdz`, final workflow) completed research (20 of 20 calls) and
  analysis, then failed: one launch-phase activity cited `CHANNEL-1`, the Strategy's own label for one of
  its channels, as its support. n8n's "Validate Strategy Grounding" found an unresolvable id and the Docs
  Writer gate refused the document. 7.7 min and at least $0.12 spent, no document.
- **Why it matters:** CrewAI's Strategy guardrail checks for exactly this ("your own ICP-/PILLAR-/CHANNEL-
  ids are never sources") and re-prompts. n8n validates after the chain node and cannot re-prompt, so a
  single bad citation costs the whole run.
- **Change:** none, deliberately: changing the workflow mid-measurement would reset the run count. Counted as
  a failure of the final workflow in the reliability figures. A possible later fix, mirroring #10, is to
  remove self-referencing ids and downgrade the claim to inference.
- **Orchestrator finding: yes,** the same limitation as #4 and #10, here at its most expensive.

## 12. Free-text service type lowers n8n's measured reproducibility

- **Found:** in the reproducibility KPI, n8n's three final runs agreed on each competitor's service type only
  33% of the time, although they meant the same thing: "cable (fiber-powered / HFC)", "hybrid (fiber & HFC)",
  "hybrid (HFC/fiber)". CrewAI's Pydantic schema allows only cable, fiber, dsl or fixed_wireless; n8n's
  structured-output schema for the Analyst allows any string.
- **Effect:** n8n's reproducibility is 78% (target 80%); with the wording mapped to CrewAI's categories it
  would be about 90%. The KPI report gives 78% as the result, because the metric was fixed before the runs.
- **Change:** none yet. The fix is an `enum` in n8n's Analyst output schema, which n8n's Structured Output
  Parser supports. It would be a new workflow version.
- **Orchestrator finding: no,** a schema choice in our n8n build.

---

## What this means for the final comparison

So far, the differences that are about **the orchestrators**, not our build:

1. **Where tool results live.** n8n's agent node routes every tool result through the model's input, which
   caps how much research one agent run can do (#7, #8) and costs tokens (execution 32: 752k input tokens for
   research alone). CrewAI's pipeline collects tool results in code. Batching (#9) works around the cap in
   n8n at the price of extra nodes and time.
2. **Recovering from a bad LLM output.** CrewAI re-prompts inside the task (guardrails); n8n validates in a
   Code node afterwards and can only repair (remove invented ids) or stop the run (#2, #4, #10, #11). On the
   final n8n workflow that meant deleting 15 and 2 invented citations in the two completed runs, and losing
   a whole run (execution 36) to one self-referencing citation.
3. **Enforcing a plan.** Both agents drift. CrewAI enforces the plan in code before the next step; n8n needs
   an explicit Code node after the agent (#5).
4. **Changing and testing it.** CrewAI changes are file edits with 358 tests run in seconds. n8n changes to
   code nodes live inside workflow JSON; deploying meant a CLI import, a restart and a manual re-publish
   (imports unpublish the workflow), and several failures (#7, #8) only showed up in paid live runs.
