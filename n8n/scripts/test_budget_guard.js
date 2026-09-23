#!/usr/bin/env node
// Runs the deployed budget guard (from the "Log: Head Planner" and "Build Evidence Records" Code nodes in
// workflows/wire3_gtm_pipeline.json) with stubbed inputs: no n8n, no LLM, no network.
// Usage (from n8n/): node scripts/test_budget_guard.js
const fs = require('fs');
const os = require('os');
const path = require('path');

const wf = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'workflows', 'wire3_gtm_pipeline.json'), 'utf8'));
const codeOf = (name) => wf.nodes.find((n) => n.name === name).parameters.jsCode;
// Only the guard: the functions between "// Budget guard" and the node's own code.
const src = codeOf('Log: Head Planner');
const guardSrc = src.slice(src.indexOf('// Budget guard'), src.indexOf('\n}\n', src.indexOf('function budgetGuard')) + 3);

function guard({ minutesAgo, budget, query = {}, searchCalls = null }) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'bg-'));
  const logged = [];
  const nodes = {
    Webhook: { body: { budget }, query },
    'Init Run Log': { _client_run_id: 'test', _pipeline_started_at: new Date(Date.now() - minutesAgo * 60000).toISOString() },
  };
  const $ = (n) => ({ first: () => ({ json: nodes[n] }) });
  const appendLog = (e) => logged.push(e);
  try {
    new Function('$', 'appendLog', `${guardSrc}\nbudgetGuard('Head Planner', ${JSON.stringify(searchCalls)});`)($, appendLog);
    return { threw: null, logged };
  } catch (e) {
    return { threw: e.message, logged };
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

let failed = 0;
const check = (name, cond, detail = '') => { console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + detail : ''}`); if (!cond) failed++; };
const brief = { max_wall_clock_minutes: 12, max_search_calls: 40 };

let r = guard({ minutesAgo: 3, budget: brief });
check('within budget: no stop', r.threw === null && r.logged.length === 0);

r = guard({ minutesAgo: 13, budget: brief });
check('over the wall clock: stops with a BUDGET error', r.threw && r.threw.startsWith('BUDGET: wall-clock budget reached'), r.threw);
check('...and logs a budget gate event', r.logged[0] && r.logged[0].gate === 'budget' && r.logged[0].status === 'error');

r = guard({ minutesAgo: 2, budget: brief, query: { budget_max_wall_clock_minutes: '1' } });
check('URL override tightens the limit for a test run', r.threw && r.threw.includes('limit 1 min'), r.threw);

r = guard({ minutesAgo: 1, budget: brief, searchCalls: 41 });
check('search calls over the cap: stops', r.threw && r.threw.startsWith('BUDGET: search-call budget exceeded'), r.threw);

r = guard({ minutesAgo: 1, budget: brief, searchCalls: 40 });
check('search calls at the cap: allowed', r.threw === null);

for (const name of ['Log: Head Planner', 'Build Evidence Records', 'Log: Analyst Agent', 'Log: Strategy Agent']) {
  check(`${name} calls the guard`, /budgetGuard\('/.test(codeOf(name).split('function budgetGuard')[1] || ''));
}
check('every chat model retries rate limits up to 6 times (a 429 once failed a whole run)',
  wf.nodes.filter((n) => n.type.includes('lmChatOpenAi')).every((n) => n.parameters.options.maxRetries === 6));
check('every chat model has a 32k completion-token cap',
  wf.nodes.filter((n) => n.type.includes('lmChatOpenAi')).every((n) => n.parameters.options.maxTokens === 32000));

console.log(failed ? `\n${failed} FAILED` : '\nall passed');
process.exit(failed ? 1 : 0);
