#!/usr/bin/env node
// Post-run token / cost summary for one n8n pipeline execution.
//
// Why this is a separate script and not a workflow node: n8n keeps each LLM sub-node's token usage in
// the stored execution record, but Code nodes cannot read sub-node data ($('OpenAI Chat Model') fails
// with "No data found from `main` input"), and the execution is only written to the database after the
// run finishes. So usage has to be read afterwards, from the database.
//
// Usage (from the n8n/ directory):
//   node scripts/run_usage.js              # latest pipeline execution
//   node scripts/run_usage.js 24           # a specific execution id
//   node scripts/run_usage.js 24 --dry-run # print only, do not append to logs/runs.jsonl
//
// Appends one `usage_summary` event to logs/runs.jsonl (skipped if that execution already has one), then the
// run's `run_record` (scripts/run_record.js).
// Requires the `sqlite3` CLI (preinstalled on macOS) and n8n's own `flatted` package.

const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { parse } = require('flatted');

const N8N_DIR = path.resolve(__dirname, '..');
const DB = path.join(N8N_DIR, '.n8n', '.n8n', 'database.sqlite');
const RUNS_LOG = path.join(N8N_DIR, 'logs', 'runs.jsonl');

// USD per 1M tokens. Verified 2026-09-19 against https://developers.openai.com/api/docs/pricing
// (standard tier). Update the value AND the date when prices change.
const PRICING = {
  'gpt-5-mini': { input: 0.25, cached_input: 0.025, output: 2.0, verified: '2026-09-19' },
};

const args = process.argv.slice(2);
const dryRun = args.includes('--dry-run');
const idArg = args.find((a) => /^\d+$/.test(a));

const sql = (q) => execFileSync('sqlite3', ['-readonly', DB, q], { maxBuffer: 256 * 1024 * 1024 }).toString();

function loadExecution(id) {
  const data = sql(`select data from execution_data where executionId=${Number(id)};`).trim();
  if (!data) return null;
  const wf = sql(`select workflowData from execution_data where executionId=${Number(id)};`).trim();
  const [status, stoppedAt] = sql(`select status, stoppedAt from execution_entity where id=${Number(id)};`).trim().split('|');
  return { id: Number(id), status, stoppedAt: stoppedAt ? new Date(stoppedAt.replace(' ', 'T') + 'Z').toISOString() : null,
           data: parse(data), workflow: JSON.parse(wf) };
}

function findExecution() {
  if (idArg) {
    const e = loadExecution(idArg);
    if (!e) throw new Error(`No stored execution with id ${idArg}`);
    return e;
  }
  const ids = sql('select id from execution_entity order by id desc limit 15;').trim().split('\n').filter(Boolean);
  for (const id of ids) {
    const e = loadExecution(id);
    if (e && e.data.resultData.runData['Init Run Log']) return e;
  }
  throw new Error('No recent execution of the pipeline found (looked for an "Init Run Log" node run).');
}

const exec = findExecution();
const runData = exec.data.resultData.runData;
const nodes = Object.fromEntries(exec.workflow.nodes.map((n) => [n.name, n]));
const conns = exec.workflow.connections;

// Which agent does each chat-model node feed?
const agentOf = {};
for (const [src, c] of Object.entries(conns)) {
  for (const group of (c.ai_languageModel || [])) for (const t of group) agentOf[src] = t.node;
}

const perAgent = [];
for (const [nodeName, runs] of Object.entries(runData)) {
  if (!agentOf[nodeName]) continue;
  let prompt = 0, completion = 0, estimated = 0, reported = 0;
  for (const r of runs) {
    const items = (r.data && r.data.ai_languageModel && r.data.ai_languageModel[0]) || [];
    for (const it of items) {
      const j = it.json || {};
      const t = j.tokenUsage || j.tokenUsageEstimate;
      if (!t) continue;
      prompt += t.promptTokens || 0;
      completion += t.completionTokens || 0;
      if (j.tokenUsage) reported++; else estimated++;
    }
  }
  const model = (nodes[nodeName].parameters.model && (nodes[nodeName].parameters.model.value || nodes[nodeName].parameters.model)) || 'unknown';
  const price = PRICING[model];
  perAgent.push({
    agent: agentOf[nodeName],
    model_node: nodeName,
    model,
    llm_calls: runs.length,
    prompt_tokens: prompt,
    completion_tokens: completion,
    tokens_source: reported && !estimated ? 'reported' : estimated && !reported ? 'estimated' : 'mixed',
    cost_usd: price ? +((prompt * price.input + completion * price.output) / 1e6).toFixed(5) : null,
  });
}

const totalPrompt = perAgent.reduce((s, a) => s + a.prompt_tokens, 0);
const totalCompletion = perAgent.reduce((s, a) => s + a.completion_tokens, 0);
const costKnown = perAgent.every((a) => a.cost_usd !== null);
const totalCost = costKnown ? +perAgent.reduce((s, a) => s + a.cost_usd, 0).toFixed(5) : null;

const init = runData['Init Run Log'] && runData['Init Run Log'][0].data.main[0][0].json;
const plan = runData['Head Planner'] && runData['Head Planner'][0].data && runData['Head Planner'][0].data.main[0][0].json.output;
const agentMeta = runData['AI Agent'] && runData['AI Agent'][0].metadata && runData['AI Agent'][0].metadata.tracing;

const event = {
  event_type: 'usage_summary',
  client_run_id: init && init._client_run_id,
  run_id: (plan && plan.run_id) || (init && init._client_run_id) || null,
  execution_id: exec.id,
  node: 'scripts/run_usage.js',
  status: exec.status,
  per_agent: perAgent,
  total_prompt_tokens: totalPrompt,
  total_completion_tokens: totalCompletion,
  estimated_llm_cost_usd: totalCost,
  cost_is_lower_bound: true,
  cost_basis: {
    pricing_usd_per_1m_tokens: PRICING,
    token_source: 'n8n tokenUsageEstimate (character-based estimate of visible text)',
  },
  cost_excludes: [
    'hidden reasoning tokens (gpt-5-mini is a reasoning model; they are billed as output but not visible to n8n)',
    'tool-call argument tokens where n8n reports 0 completion tokens',
    'search-provider fees (Tavily/SerpAPI)',
  ],
  search_tool_calls_completed: agentMeta ? agentMeta['ai.agent.tool_calls.completed'] ?? null : null,
};

// ---- print ----
const pad = (s, n) => String(s).padEnd(n);
console.log(`Execution ${exec.id} (${exec.status})  client_run_id=${event.client_run_id}  run_id=${event.run_id}`);
console.log(pad('agent', 20) + pad('model', 14) + pad('calls', 7) + pad('prompt tok', 12) + pad('compl tok', 11) + pad('source', 11) + 'cost USD');
for (const a of perAgent) {
  console.log(pad(a.agent, 20) + pad(a.model, 14) + pad(a.llm_calls, 7) + pad(a.prompt_tokens, 12) + pad(a.completion_tokens, 11) + pad(a.tokens_source, 11) + (a.cost_usd ?? 'n/a (no price for model)'));
}
console.log(pad('TOTAL', 34) + pad(totalPrompt, 12) + pad(totalCompletion, 11) + pad('', 11) + (totalCost ?? 'n/a'));
console.log('\nLOWER BOUND: excludes hidden reasoning tokens and search-provider fees. Compare with the OpenAI usage dashboard.');

if (dryRun) {
  console.log('(dry run: nothing written)');
} else {
  const existing = fs.existsSync(RUNS_LOG) ? fs.readFileSync(RUNS_LOG, 'utf8') : '';
  const dup = existing.split('\n').some((l) => l.includes('"event_type":"usage_summary"') && l.includes(`"execution_id":${exec.id},`));
  if (dup) console.log(`usage_summary for execution ${exec.id} already in logs/runs.jsonl -- not appended again.`);
  else {
    fs.mkdirSync(path.dirname(RUNS_LOG), { recursive: true });
    fs.appendFileSync(RUNS_LOG, JSON.stringify({ ts: new Date().toISOString(), implementation: 'n8n', ...event }) + '\n');
    console.log('Appended usage_summary to logs/runs.jsonl');
  }
  // A run that failed mid-workflow never reaches Finalize Run, so it wrote no run_complete. Write one here, from
  // the stored execution, so every run -- failed ones too -- gets a run_record. (An n8n error workflow would not
  // do: n8n does not trigger it for manual/test-webhook executions.)
  const after = fs.readFileSync(RUNS_LOG, 'utf8');
  const hasComplete = after.split('\n').some((l) => l.includes('"event_type":"run_complete"') && l.includes(`"client_run_id":"${event.client_run_id}"`));
  if (!hasComplete && ['error', 'crashed', 'canceled'].includes(exec.status)) {
    const err = exec.data.resultData.error || {};
    const evidence = runData['Build Evidence Records'] && runData['Build Evidence Records'][0].data
      && runData['Build Evidence Records'][0].data.main[0][0].json;
    const budget = (plan && plan.budget) || {};
    const startedAt = init && init._pipeline_started_at;
    fs.appendFileSync(RUNS_LOG, JSON.stringify({
      ts: exec.stoppedAt || new Date().toISOString(), implementation: 'n8n', event_type: 'run_complete',
      client_run_id: event.client_run_id, run_id: event.run_id, node: 'scripts/run_usage.js', status: 'failed',
      failed_step: exec.data.resultData.lastNodeExecuted || null, error: String(err.message || exec.status).slice(0, 500),
      duration_ms: startedAt && exec.stoppedAt ? new Date(exec.stoppedAt) - new Date(startedAt) : null,
      model: 'gpt-5-mini', provider: 'openai',
      research_questions_total: plan && plan.research_questions ? plan.research_questions.length : null,
      research_questions_answered: evidence ? new Set(evidence.evidence.map((e) => e.research_question_id).filter(Boolean)).size : null,
      evidence_count: evidence ? evidence.evidence_count : null,
      tool_calls_planned: evidence ? evidence.tool_calls_planned : null, tool_calls_executed: evidence ? evidence.tool_calls_executed : null,
      document_write_status: 'not_run', document_url: null,
      budget_max_wall_clock_minutes: budget.max_wall_clock_minutes ?? null, budget_max_search_calls: budget.max_search_calls ?? null,
      budget_max_cost_usd: budget.max_cost_usd ?? null, issues: [],
    }) + '\n');
    console.log(`Execution ${exec.id} failed at ${exec.data.resultData.lastNodeExecuted}: appended run_complete (status failed)`);
  }
  // the run's comparable summary (schemas/run_record.schema.json); needs usage_summary, so it goes last
  const webhook = runData.Webhook && runData.Webhook[0].data.main[0][0].json;
  const written = require('./run_record').appendFor(event.client_run_id, { briefFor: () => (webhook ? webhook.body : null) });
  console.log(written.length ? 'Appended run_record to logs/runs.jsonl' : 'run_record already in logs/runs.jsonl -- not appended again.');
}
