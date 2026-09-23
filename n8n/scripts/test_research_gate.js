#!/usr/bin/env node
// Runs the deployed "Log: Head Planner" plan gate and "Build Evidence Records" code (taken from
// workflows/wire3_gtm_pipeline.json, so the test can't drift from what n8n runs) against execution 29's real
// plan and Research Agent steps. Execution 29 planned no Wire3 research, put topics in company_name, and
// executed its 13 planned calls twice plus a validate_source call. No n8n, no network.
// Usage (from n8n/): node scripts/test_research_gate.js
const fs = require('fs');
const os = require('os');
const path = require('path');

const wf = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'workflows', 'wire3_gtm_pipeline.json'), 'utf8'));
const codeOf = (name) => wf.nodes.find((n) => n.name === name).parameters.jsCode;
const fx = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'execution29_research.json'), 'utf8'));
const clone = (x) => JSON.parse(JSON.stringify(x));

function runNode(name, input, nodes) {
  const cwd = process.cwd();
  process.chdir(fs.mkdtempSync(path.join(os.tmpdir(), 'research-gate-'))); // the node appends to ./logs/runs.jsonl
  try {
    const $ = (n) => ({ first: () => ({ json: nodes[n] }) });
    const inputs = Array.isArray(input) ? input : [input];
    const $input = { first: () => ({ json: inputs[0] }), all: () => inputs.map((json) => ({ json })) };
    const out = new Function('$', '$input', 'require', codeOf(name))($, $input, require);
    return Array.isArray(input) || name === 'Split Plan by Company' ? out.map((o) => o.json) : out[0].json;
  } finally {
    process.chdir(cwd);
  }
}

const now = new Date().toISOString();
const baseNodes = {
  Webhook: { body: fx.brief, query: {} },
  'Init Run Log': { _client_run_id: 'test', _pipeline_started_at: now, _t_head_planner_start: now },
};
const gate = (plan) => runNode('Log: Head Planner', { output: plan }, baseNodes);

let failed = 0;
const check = (name, cond, detail = '') => { console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + detail : ''}`); if (!cond) failed++; };
const throws = (fn) => { try { fn(); return null; } catch (e) { return e; } };

// --- plan gate ---------------------------------------------------------------------------------------
let err = throws(() => gate(fx.plan));
check('execution 29 plan: rejected', err && /plan rejected/.test(err.message), err && err.message.slice(0, 140));
check('execution 29 plan: names the topic placeholders', err && /Regional fiber ISPs/.test(err.message) && /Marketing channels/.test(err.message));
check('execution 29 plan: says Wire3 was not researched', err && /no Wire3 research calls/.test(err.message));

// the same plan with its topic calls re-pointed at Wire3 passes
const fixed = clone(fx.plan);
fixed.planned_tool_calls.forEach((c) => { if (!fx.brief.competitors.includes(c.args.company_name)) c.args.company_name = 'Wire3'; });
err = throws(() => gate(fixed));
check('plan targeting Wire3 and the competitors: accepted', !err, err && err.message);

// company names are matched case-insensitively, as CrewAI's guardrail does
const cased = clone(fixed);
cased.planned_tool_calls[0].args.company_name = cased.planned_tool_calls[0].args.company_name.toUpperCase();
check('company names match case-insensitively', !throws(() => gate(cased)));

// only competitors, no Wire3: rejected even though every name is allowed
const noWire3 = clone(fixed);
noWire3.planned_tool_calls = noWire3.planned_tool_calls.filter((c) => c.args.company_name !== 'Wire3');
err = throws(() => gate(noWire3));
check('competitors-only plan: rejected for missing Wire3', err && /no Wire3 research calls/.test(err.message) && !/must be Wire3 or one of/.test(err.message));

// --- evidence: repeated and unplanned calls ----------------------------------------------------------
const evNodes = {
  ...baseNodes,
  'Log: Head Planner': { _client_run_id: 'test', _run_id: 'test', _t_research_agent_start: now },
  'Head Planner': { output: fx.plan },
};
const out = runNode('Build Evidence Records', { intermediateSteps: fx.intermediate_steps }, evNodes);
const noRq = out.evidence.filter((e) => !e.research_question_id).length;
check('execution 29 steps: 27 executed, 13 planned', out.tool_calls_executed === 27 && out.tool_calls_planned === 13,
  `${out.tool_calls_executed} executed / ${out.tool_calls_planned} planned`);
check('repeats and validate_source produce no evidence', noRq === 0, `${noRq} records without a research question (was 130)`);
check('every record serves a research question', out.evidence.every((e) => /^RQ\d+$/.test(e.research_question_id)));

// the first execution of each call is kept: evidence equals a run of the 13 planned calls alone
const firstOnly = runNode('Build Evidence Records', { intermediateSteps: fx.intermediate_steps.slice(0, 13) }, evNodes);
check('evidence equals the 13 planned calls run once',
  JSON.stringify(out.evidence.map((e) => e.evidence_id)) === JSON.stringify(firstOnly.evidence.map((e) => e.evidence_id)),
  `${out.evidence.length} vs ${firstOnly.evidence.length} records`);

// --- research one company at a time -----------------------------------------------------------------
const batches = runNode('Split Plan by Company', { output: fixed, _client_run_id: 'test' }, baseNodes);
const companies = batches.map((b) => b.output.planned_tool_calls[0].args.company_name);
check('split: one batch per company, Wire3 first', companies[0] === 'Wire3' && new Set(companies).size === batches.length
  && batches.length === 4, JSON.stringify(companies));
check('split: every planned call lands in exactly one batch',
  batches.reduce((n, b) => n + b.output.planned_tool_calls.length, 0) === fixed.planned_tool_calls.length);
check('split: batches carry the run context', batches.every((b) => b._client_run_id === 'test' && b._research_batch.of === 4));

// the agent's steps, as if it had run once per company, merged back by Collect Research Steps
const company = (st) => String((st.action.toolInput || {}).company_name || '');
const perCompany = [...new Set(fx.intermediate_steps.slice(0, 13).map(company))]
  .map((c) => ({ output: `done ${c}`, intermediateSteps: fx.intermediate_steps.slice(0, 13).filter((st) => company(st) === c) }));
const merged = runNode('Collect Research Steps', perCompany, baseNodes)[0];
check('collect: every step of every batch is kept', merged.intermediateSteps.length === 13 && merged.research_batches === perCompany.length,
  `${merged.intermediateSteps.length} steps from ${merged.research_batches} batches`);
const fromBatches = runNode('Build Evidence Records', { intermediateSteps: merged.intermediateSteps }, evNodes);
const ids = (o) => JSON.stringify(o.evidence.map((e) => e.evidence_id).sort());
check('evidence from per-company runs equals one run of the same calls', ids(fromBatches) === ids(firstOnly),
  `${fromBatches.evidence.length} records`);

const wait = wf.nodes.find((n) => n.name === 'Pause Between Companies').parameters;
check('pause is 15 seconds, not the node\'s default unit (hours)', wait.amount === 15 && wait.unit === 'seconds');
const loopOut = wf.connections['Research Loop'].main;
check('loop wiring: done -> Collect Research Steps, loop -> AI Agent',
  loopOut[0][0].node === 'Collect Research Steps' && loopOut[1][0].node === 'AI Agent'
  && wf.connections['AI Agent'].main[0][0].node === 'Pause Between Companies'
  && wf.connections['Pause Between Companies'].main[0][0].node === 'Research Loop');

console.log(failed ? `\n${failed} check(s) failed` : '\nAll checks passed');
process.exit(failed ? 1 : 0);
