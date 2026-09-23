// Local isolated test for the Docs Writer Code nodes -- no n8n, no network, no LLM cost.
// Usage: node docs_writer/test_local.js <dir with docs_input.json, analyst_item.json, evidence.json>
// Runs the two node bodies with a stubbed $input / $() and a mock Google Docs API that applies the
// batchUpdate requests, then runs negative cases (orphan citation, tampered doc, non-accepted input).
const fs = require('fs');
const path = require('path');
const os = require('os');
const dir = path.resolve(process.argv[2]);
// The node code appends to ./logs/runs.jsonl; run from a scratch directory so tests never touch n8n's real log.
process.chdir(fs.mkdtempSync(path.join(os.tmpdir(), 'n8n-docs-test-')));
const load = (f) => JSON.parse(fs.readFileSync(path.join(dir, f), 'utf8'));
const docsInput = load('docs_input.json');
const analystItem = load('analyst_item.json');
const evidence = load('evidence.json');
const planner = fs.existsSync(path.join(dir, 'planner.json')) ? load('planner.json') : { output: {} };

const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const src = (f) => fs.readFileSync(path.join(__dirname, f), 'utf8');
const run = async (file, inputJson, nodes) => {
  const fn = new AsyncFunction('$input', '$', 'require', src(file));
  return fn({ first: () => ({ json: inputJson }) }, (name) => ({ first: () => ({ json: nodes[name] }) }), require);
};

// Mock Docs API: applies insertText + updateTextStyle(link) to produce a doc like the real GET would.
function mockDoc(title, requests) {
  let body = '';
  const links = [];
  for (const r of requests) {
    if (r.insertText) body = body.slice(0, r.insertText.location.index - 1) + r.insertText.text + body.slice(r.insertText.location.index - 1);
    if (r.updateTextStyle && r.updateTextStyle.textStyle.link) links.push(r.updateTextStyle);
  }
  const els = [];
  let cur = 0; // 0-based offset into body
  for (const l of links.sort((a, b) => a.range.startIndex - b.range.startIndex)) {
    const s = l.range.startIndex - 1, e = l.range.endIndex - 1;
    if (s > cur) els.push({ textRun: { content: body.slice(cur, s) } });
    els.push({ textRun: { content: body.slice(s, e), textStyle: { link: l.textStyle.link } } });
    cur = e;
  }
  els.push({ textRun: { content: body.slice(cur) } });
  return { documentId: 'DOC123', title, body: { content: [{ paragraph: { elements: els } }] } };
}

let failed = 0;
const check = (name, cond, detail = '') => { console.log(`${cond ? 'PASS' : 'FAIL'}  ${name}${detail ? '  -- ' + detail : ''}`); if (!cond) failed++; };
const nodes = { 'Validate Evidence Coverage': analystItem, 'Build Evidence Records': { evidence }, 'Head Planner': planner };
const accepted = { status: 'accepted', brief_id: docsInput.artifact.brief_id, artifact: docsInput.artifact, _client_run_id: 'test-client', _run_id: 'test-run', _t_docs_writer_start: new Date().toISOString() };

(async () => {
  // 1. Happy path
  const [{ json: built }] = await run('build_docs_content.js', accepted, nodes);
  const doc = mockDoc(built.title, built.requests);
  const text = doc.body.content[0].paragraph.elements.map((e) => e.textRun.content).join('');
  check('build: produced requests', built.requests.length > 10, `${built.requests.length} requests`);
  check('build: all 19 sections (same layout as CrewAI)', built.expected.section_headings.length === 19, built.expected.section_headings.join(' | ').slice(0, 120));
  check('build: analysis sections rendered', /3\. Competitor comparison/.test(text) && /5\. Pricing matrix/.test(text));
  check('build: pricing plan-name citations shown', /Plan name: .+\[EV-/.test(text));
  check('build: sources cited', built.expected.source_ids.length > 0, `${built.expected.source_ids.length} sources, ${built.expected.link_urls.length} links`);
  check('build: no undefined/[object Object] in text', !/undefined|\[object Object\]/.test(text));
  check('build: every request index in range', built.requests.every((r) => { const g = r.updateParagraphStyle || r.updateTextStyle || r.createParagraphBullets; return !g || (g.range.startIndex >= 1 && g.range.endIndex <= text.length + 1); }));
  const [{ json: verified }] = await run('verify_document.js', doc, { ...nodes, 'Build Docs Content': built, 'Docs: Create Document': { documentId: 'DOC123' } });
  check('verify: passes on faithful doc', verified.docs_writer_status === 'document_created' && !!verified.artifact, verified.document_url);
  fs.writeFileSync(path.join(dir, 'rendered_doc.txt'), text);

  // 2. Negative: tampered doc (drop a section heading + an inserted orphan id)
  const bad = JSON.parse(JSON.stringify(doc));
  bad.body.content[0].paragraph.elements[0].textRun.content = bad.body.content[0].paragraph.elements[0].textRun.content.replace('16. Success metrics', '16. X').concat(' [EV-deadbeef]');
  let err = null;
  try { await run('verify_document.js', bad, { ...nodes, 'Build Docs Content': built, 'Docs: Create Document': { documentId: 'DOC123' } }); } catch (e) { err = e; }
  check('verify: rejects missing heading + orphan id', err && /Missing section heading/.test(err.message) && /Orphaned/.test(err.message));

  // 3. Negative: orphan citation upstream (strategy cites an EV id that is not in the evidence set)
  const orphanArt = JSON.parse(JSON.stringify(docsInput.artifact));
  orphanArt.icps[0].description.supporting_ids.push('EV-00000000');
  err = null;
  try { await run('build_docs_content.js', { ...accepted, artifact: orphanArt }, nodes); } catch (e) { err = e; }
  check('build: rejects orphaned citation', err && /Orphaned citation/.test(err.message), err && err.message.slice(0, 90));

  // 4. Negative: unresolvable id
  const badId = JSON.parse(JSON.stringify(docsInput.artifact));
  badId.icps[0].description.supporting_ids.push('THEME-999');
  err = null;
  try { await run('build_docs_content.js', { ...accepted, artifact: badId }, nodes); } catch (e) { err = e; }
  check('build: rejects unresolvable id', err && /Unresolvable/.test(err.message));

  // 5. Negative: gate bypass
  err = null;
  try { await run('build_docs_content.js', { ...accepted, status: 'rejected' }, nodes); } catch (e) { err = e; }
  check('build: refuses non-accepted input', err && /not marked accepted/.test(err.message));

  console.log(failed ? `\n${failed} FAILED` : '\nAll checks passed');
  process.exit(failed ? 1 : 0);
})();
