#!/usr/bin/env node
// Runs the deployed "Validate Evidence Coverage" code (taken from workflows/wire3_gtm_pipeline.json, so the test
// can't drift from what n8n runs) against execution 28's real Analyst output, which cited an invented EV-CNHA,
// plus synthetic cases. No n8n, no network. Usage (from n8n/): node scripts/test_evidence_gate.js
const fs = require('fs');
const os = require('os');
const path = require('path');

const wf = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'workflows', 'wire3_gtm_pipeline.json'), 'utf8'));
const code = wf.nodes.find((n) => n.name === 'Validate Evidence Coverage').parameters.jsCode;
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixtures', 'execution28_evidence_gate.json'), 'utf8'));

function runGate(artifact, validIds) {
  const cwd = process.cwd();
  process.chdir(fs.mkdtempSync(path.join(os.tmpdir(), 'gate-'))); // the node appends to ./logs/runs.jsonl
  try {
    const ctx = { _client_run_id: 'test', _run_id: 'test', evidence: validIds.map((id) => ({ evidence_id: id, source_url: 'https://example.com/x' })) };
    const $ = () => ({ first: () => ({ json: ctx }) });
    const $input = { first: () => ({ json: { output: JSON.parse(JSON.stringify(artifact)) } }) };
    return new Function('$', '$input', 'require', code)($, $input, require)[0].json;
  } finally {
    process.chdir(cwd);
  }
}

let failed = 0;
const check = (name, cond, detail = '') => { console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + detail : ''}`); if (!cond) failed++; };

// 1. the real failure: EV-CNHA in SWOT-O1 next to a real id -> removed, run continues
const out = runGate(fixture.analyst_output, fixture.evidence_ids);
const o1 = out.artifact.swot.opportunities.find((i) => i.item_id === 'SWOT-O1');
check('execution 28: EV-CNHA removed from SWOT-O1', !o1.evidence_ids.includes('EV-CNHA') && o1.evidence_ids.length === 1, JSON.stringify(o1.evidence_ids));
check('execution 28: basis kept (a real id remains)', o1.basis === 'evidence');
check('execution 28: every remaining citation is real', out.validation.all_references_valid && out.validation.valid_reference_coverage_percent === 100);
// execution 28 also cited EV-ed489c6 (7 hex digits, one short) in 5 places; the old ^EV-[a-f0-9]{8}$ check saw neither
const dropped = out.validation.dropped_ids.flatMap((d) => d.dropped);
check('execution 28: both invented ids removed and reported', dropped.filter((i) => i === 'EV-CNHA').length === 1
  && dropped.filter((i) => i === 'EV-ed489c6').length === 5, JSON.stringify(dropped));
check('execution 28: claims left uncited fell to inference', out.validation.dropped_ids.filter((d) => d.basis_downgraded_to_inference).length === 2);

// 2. a claim whose only citation was invented falls to inference
const valid = ['EV-0000000a'];
const lone = runGate({ swot: { strengths: [{ item_id: 'SWOT-S1', statement: 's', basis: 'evidence', evidence_ids: ['EV-zzzz'] }] } }, valid);
check('only citation invented -> basis inference', lone.artifact.swot.strengths[0].basis === 'inference' && lone.artifact.swot.strengths[0].evidence_ids.length === 0);

// 3. a theme must keep a real id: if its only one is invented, the gate stops the run
let threw = null;
try { runGate({ market_themes: [{ theme_id: 'THEME-1', supporting_evidence_ids: ['EV-bogus'] }] }, valid); } catch (e) { threw = e.message; }
check('theme left with no real evidence -> run stops here', threw && threw.includes('EV-bogus'), threw || 'did not throw');

// 4. a well-formed but unknown id is also removed
const wellFormed = runGate({ x: { basis: 'evidence', evidence_ids: ['EV-0000000a', 'EV-deadbeef'] } }, valid);
check('well-formed unknown id removed too', JSON.stringify(wellFormed.artifact.x.evidence_ids) === '["EV-0000000a"]');

// 5. a clean artifact is untouched
const clean = runGate({ x: { basis: 'evidence', evidence_ids: ['EV-0000000a'] } }, valid);
check('clean artifact: nothing dropped', clean.validation.dropped_ids.length === 0);

console.log(failed ? `\n${failed} FAILED` : '\nall passed');
process.exit(failed ? 1 : 0);
