#!/usr/bin/env node
// One comparable run_record per run (schemas/run_record.schema.json), built from the run's own events in
// logs/runs.jsonl plus, for the brief id, the brief the Webhook received in the stored execution.
// CrewAI builds the same record in crewai/wire3_gtm/run_record.py; the schema is the contract between them.
//
// Usage (from the n8n/ directory):
//   node scripts/run_record.js            # add a run_record to every finished run that lacks one
//   node scripts/run_record.js --dry-run  # print them instead
// run_usage.js calls appendFor() after it writes usage_summary, so new runs get one automatically.

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const RUNS_LOG = path.join(__dirname, '..', 'logs', 'runs.jsonl');
const AGENTS = ['Head Planner', 'Research Agent', 'Analyst Agent', 'Strategy Agent', 'Docs Writer'];
const TOOL_PREFIX = /^MCP_Research_Tools_/;

// Fields n8n cannot measure: null in the record, with the reason, never 0.
const NOT_MEASURED = {
  search_provider: 'not logged by the workflow; the MCP server picks Tavily or SerpAPI from its own env',
  'agents[].reasoning_tokens': 'n8n token counts are character estimates of visible text; hidden reasoning tokens are not exposed',
  'agents[].retries': 'n8n stores no per-node attempt counter',
  'tools.retried': 'n8n stores no per-node attempt counter',
  'retries.total': 'no true retry count; retries.provider is the HTTP 429 count from the server log (approximate)',
  'retries.tool_call': 'n8n stores no per-node attempt counter',
  'retries.guardrail': 'the Structured Output Parser re-prompts internally without logging it',
  'tokens.reasoning': 'hidden reasoning tokens are not exposed to n8n',
  'tokens.cached_prompt': 'not exposed to n8n',
  'links.checked': 'n8n validates URL format only; it does not request the URLs',
  'links.broken': 'n8n validates URL format only; it does not request the URLs',
  'links.blocked': 'n8n validates URL format only; it does not request the URLs',
  'links.unverified': 'n8n validates URL format only; it does not request the URLs',
};

// Canonical JSON: keys sorted at every level, no whitespace. Matches Python's
// json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False).
function canonical(v) {
  if (Array.isArray(v)) return `[${v.map(canonical).join(',')}]`;
  if (v && typeof v === 'object') return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canonical(v[k])}`).join(',')}}`;
  return JSON.stringify(v);
}

const briefId = (brief) => (brief == null ? null
  : 'brief-' + crypto.createHash('sha256').update(canonical(brief), 'utf8').digest('hex').slice(0, 12));

const last = (events, type, match = {}) => {
  const found = events.filter((e) => e.event_type === type && Object.entries(match).every(([k, v]) => e[k] === v));
  return found.length ? found[found.length - 1] : null;
};

function build(events, brief, now = new Date()) {
  const rc = last(events, 'run_complete');
  if (!rc) throw new Error('run has no run_complete event');
  const us = last(events, 'usage_summary') || {};
  const start = last(events, 'pipeline_start') || {};
  const verify = last(events, 'document_write', { node: 'Verify Document & Log' });
  const placeholder = last(events, 'document_write', { status: 'placeholder_ok' });

  // usage_summary names agents by n8n node ("AI Agent"); agent_start maps node -> agent
  const agentOfNode = {};
  for (const e of events) if (e.event_type === 'agent_start' && e.agent) agentOfNode[e.node] = e.agent;
  const usage = {};
  for (const a of us.per_agent || []) usage[agentOfNode[a.agent] || a.agent] = a;
  const noLlm = (name) => (name === 'Docs Writer' ? 0 : null);

  const agents = [];
  for (const name of AGENTS) {
    const s = last(events, 'agent_start', { agent: name });
    const e = last(events, 'agent_end', { agent: name });
    if (!s && !e) continue;
    const u = usage[name] || {};
    agents.push({
      agent: name, status: e ? e.status : 'started',
      started_at: s ? s.ts : null, ended_at: e ? e.ts : null, duration_ms: e && e.duration_ms != null ? e.duration_ms : null,
      llm_calls: u.llm_calls ?? noLlm(name), prompt_tokens: u.prompt_tokens ?? noLlm(name),
      completion_tokens: u.completion_tokens ?? noLlm(name), reasoning_tokens: name === 'Docs Writer' ? 0 : null,
      cost_usd: u.cost_usd ?? noLlm(name), retries: null, error: e && e.error ? String(e.error) : null,
    });
  }

  const byTool = {};
  const toolCalls = events.filter((e) => e.event_type === 'tool_call');
  for (const t of toolCalls) {
    const k = String(t.tool).replace(TOOL_PREFIX, '');
    byTool[k] = byTool[k] || { calls: 0, errors: 0 };
    byTool[k].calls += 1;
    byTool[k].errors += t.status !== 'ok' ? 1 : 0;
  }
  const research = last(events, 'agent_end', { agent: 'Research Agent' }) || {};

  const errors = events
    .filter((e) => ['error', 'failed'].includes(e.status) && e.event_type !== 'usage_summary')
    .map((e) => ({ event_type: e.event_type, node: e.node ?? null, message: String(e.error || e.gate || e.status).slice(0, 500) }));

  const writeStatus = verify ? (verify.status === 'ok' ? 'verified' : 'failed')
    : placeholder ? 'placeholder' : rc.document_write_status && rc.document_write_status !== 'not_run' ? 'failed' : 'not_run';
  const sources = (us.per_agent || []).map((a) => a.tokens_source);
  const cost = us.estimated_llm_cost_usd ?? null;
  const maxMin = rc.budget_max_wall_clock_minutes ?? null;
  const maxSearch = rc.budget_max_search_calls ?? null;

  return {
    schema_version: 1, event_type: 'run_record', ts: now.toISOString(),
    implementation: 'n8n', run_id: rc.client_run_id, brief_id: briefId(brief), planner_run_id: rc.run_id ?? null,
    started_at: start.ts ?? null, ended_at: rc.ts, duration_ms: rc.duration_ms ?? null,
    status: rc.status, failed_step: rc.failed_step ?? null, errors, issues: rc.issues || [],
    model: rc.model ?? null, provider: rc.provider ?? null, search_provider: null,
    agents,
    tools: { planned: rc.tool_calls_planned ?? null, executed: rc.tool_calls_executed ?? null,
             failed: research.tool_calls_failed ?? null, retried: null, by_tool: byTool },
    retries: { total: null, tool_call: null, guardrail: null, provider: rc.rate_limit_errors_logged ?? null, basis: 'approximate' },
    tokens: { prompt: us.total_prompt_tokens ?? null, completion: us.total_completion_tokens ?? null, reasoning: null, cached_prompt: null,
              source: !sources.length ? 'unavailable' : sources.every((s) => s === 'reported') ? 'provider_reported' : 'estimated' },
    cost: { estimated_llm_usd: cost, is_lower_bound: us.cost_is_lower_bound ?? null, excludes: us.cost_excludes || [] },
    research: { questions_total: rc.research_questions_total ?? null, questions_answered: rc.research_questions_answered ?? null },
    evidence: { records: rc.evidence_count ?? null, coverage_percent: rc.evidence_coverage_percent ?? null },
    links: { checked: null, broken: null, invalid: rc.invalid_url_count ?? null, blocked: null, unverified: null },
    document: { write_status: writeStatus, verified: writeStatus === 'verified', url: rc.document_url ?? null,
                sections: verify ? verify.section_count ?? null : null, sources_cited: verify ? verify.source_count ?? null : null },
    budget: { max_cost_usd: rc.budget_max_cost_usd ?? null, max_search_calls: maxSearch, max_wall_clock_minutes: maxMin,
              search_calls: toolCalls.length,
              within_latency: rc.duration_ms != null && maxMin ? rc.duration_ms <= maxMin * 60000 : null,
              within_cost: cost != null && rc.budget_max_cost_usd ? cost <= rc.budget_max_cost_usd : null,
              within_search_calls: maxSearch ? toolCalls.length <= maxSearch : null },
    not_measured: NOT_MEASURED,
  };
}

function eventsByRun(text) {
  const runs = {};
  for (const line of text.split('\n')) {
    if (!line.trim()) continue;
    const e = JSON.parse(line);
    const key = e.event_type === 'run_record' ? e.run_id : e.client_run_id;
    (runs[key] = runs[key] || []).push(e);
  }
  return runs;
}

// The brief this run was given, from the stored execution's Webhook node; null if the execution is gone.
function briefFromExecution(executionId) {
  if (executionId == null) return null;
  try {
    const { execFileSync } = require('child_process');
    const { parse } = require('flatted');
    const db = path.join(__dirname, '..', '.n8n', '.n8n', 'database.sqlite');
    const raw = execFileSync('sqlite3', ['-readonly', db, `select data from execution_data where executionId=${Number(executionId)};`],
      { maxBuffer: 256 * 1024 * 1024 }).toString().trim();
    return raw ? parse(raw).resultData.runData.Webhook[0].data.main[0][0].json.body : null;
  } catch (err) {
    return null;
  }
}

// Append a record for one run (or every run lacking one). Returns the client_run_ids written.
function appendFor(clientRunId = null, { dryRun = false, briefFor = null } = {}) {
  const runs = eventsByRun(fs.readFileSync(RUNS_LOG, 'utf8'));
  const done = [];
  for (const [cid, events] of Object.entries(runs)) {
    if (clientRunId && cid !== clientRunId) continue;
    const types = new Set(events.map((e) => e.event_type));
    if (!types.has('run_complete') || types.has('run_record')) continue;
    const usage = last(events, 'usage_summary');
    const brief = briefFor ? briefFor(cid) : briefFromExecution(usage && usage.execution_id);
    const rec = build(events, brief);
    if (dryRun) console.log(JSON.stringify(rec, null, 1));
    else fs.appendFileSync(RUNS_LOG, JSON.stringify(rec) + '\n');
    done.push(cid);
  }
  return done;
}

module.exports = { build, briefId, canonical, eventsByRun, appendFor, NOT_MEASURED };

if (require.main === module) {
  const done = appendFor(null, { dryRun: process.argv.includes('--dry-run') });
  console.log(done.length ? `run_record ${process.argv.includes('--dry-run') ? 'built' : 'appended'} for: ${done.join(', ')}` : 'every finished run already has a run_record');
}
