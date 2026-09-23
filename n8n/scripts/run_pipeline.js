#!/usr/bin/env node
// Start one n8n pipeline run with the shared brief (repo-root brief.json, the same file CrewAI reads),
// so both implementations always get the identical brief. Posting a brief by hand is how they drifted:
// see schemas/run_record.md, "Known comparability gaps".
//
// Usage (from the n8n/ directory, with n8n running):
//   node scripts/run_pipeline.js          # production webhook: the workflow must be Active
//   node scripts/run_pipeline.js --test   # test webhook: click "Execute workflow" in the editor first
// Then, as before: node scripts/run_usage.js   (usage_summary + run_record)
//
// The webhook answers when the last node finishes (responseMode lastNode), so this waits for the whole run.

const fs = require('fs');
const path = require('path');

const BRIEF = path.resolve(__dirname, '..', '..', 'brief.json');
const BASE = process.env.N8N_URL || 'http://localhost:5678';
const PATH = 'wire3-gtm-brief'; // the Webhook node's path in workflows/wire3_gtm_pipeline.json

(async () => {
  const body = fs.readFileSync(BRIEF, 'utf8');
  JSON.parse(body); // fail here, not inside n8n, if the file is not valid JSON
  const url = `${BASE}/${process.argv.includes('--test') ? 'webhook-test' : 'webhook'}/${PATH}`;
  console.log(`POST ${path.relative(process.cwd(), BRIEF)} -> ${url}\n(waits for the whole run, about 4-8 minutes)`);
  const t0 = Date.now();
  // plain http, not fetch: fetch gives up after 5 minutes without response headers, and this webhook
  // only answers when the run ends (the run itself carries on in n8n either way)
  const res = await new Promise((resolve, reject) => {
    const req = require(url.startsWith('https') ? 'https' : 'http').request(url,
      { method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } },
      (r) => { let t = ''; r.on('data', (c) => { t += c; }); r.on('end', () => resolve({ status: r.statusCode, text: t })); });
    req.setTimeout(20 * 60 * 1000, () => req.destroy(new Error('no response after 20 minutes; check the n8n executions list')));
    req.on('error', reject);
    req.end(body);
  });
  res.ok = res.status >= 200 && res.status < 300;
  const text = res.text;
  console.log(`HTTP ${res.status} after ${Math.round((Date.now() - t0) / 1000)} s`);
  console.log(text.slice(0, 2000));
  if (res.status === 404) {
    console.log('\n404: the workflow is not Active (production URL), or the editor is not listening (--test). '
      + 'Activate it, or open it and click "Execute workflow", then rerun.');
  }
  if (!res.ok) process.exit(1);
  console.log('\nNext: node scripts/run_usage.js   (appends usage_summary and run_record)');
})().catch((err) => { console.error(err.message); process.exit(1); });
