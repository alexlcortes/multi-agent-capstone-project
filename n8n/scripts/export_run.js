#!/usr/bin/env node
// Export one finished n8n execution for the KPI measurement (crewai/wire3_gtm/kpi.py): the evidence set,
// Analyst and Strategy artifacts, and the Google Doc exactly as n8n read it back after writing, as Markdown.
// Reads n8n's local database read-only; no network, no Google.
//
// Usage (from n8n/): node scripts/export_run.js <executionId>
//   -> ../eval/kpi/data/n8n_<client_run_id>.json and .md
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');
const { parse } = require('flatted');

const id = Number(process.argv[2]);
if (!id) { console.error('usage: node scripts/export_run.js <executionId>'); process.exit(2); }
const DB = path.join(__dirname, '..', '.n8n', '.n8n', 'database.sqlite');
const raw = execFileSync('sqlite3', [`file:${DB}?mode=ro`, `select data from execution_data where executionId=${id};`],
  { maxBuffer: 1 << 30 }).toString().trim();
if (!raw) { console.error(`no data for execution ${id}`); process.exit(1); }
const runData = parse(raw).resultData.runData;
const out = (node) => runData[node] && runData[node].at(-1).data.main[0][0].json;

const init = out('Init Run Log');
const readBack = out('Docs: Read Back');
if (!readBack) { console.error(`execution ${id} has no document read-back (did it finish?)`); process.exit(1); }

// Google Docs 'get' JSON -> Markdown: headings by named style, bullets, and links on their text.
const prefix = { TITLE: '# ', HEADING_1: '# ', HEADING_2: '\n## ', HEADING_3: '\n### ' };
const lines = [];
for (const el of readBack.body.content) {
  const p = el.paragraph;
  if (!p) continue;
  let text = '';
  for (const r of p.elements) {
    const tr = r.textRun;
    if (!tr) continue;
    const c = tr.content.replace(/\n$/, '');
    const url = tr.textStyle && tr.textStyle.link && tr.textStyle.link.url;
    text += url && c ? `[${c}](${url})` : c;
  }
  if (!text.trim()) continue;
  const style = (p.paragraphStyle || {}).namedStyleType || '';
  lines.push((prefix[style] || (p.bullet ? '- ' : '')) + text);
}

const clientRunId = init._client_run_id;
const dir = path.join(__dirname, '..', '..', 'eval', 'kpi', 'data');
fs.mkdirSync(dir, { recursive: true });
const base = path.join(dir, `n8n_${clientRunId}`);
fs.writeFileSync(`${base}.json`, JSON.stringify({
  source: `n8n execution ${id} (run ${clientRunId})`,
  evidence: out('Build Evidence Records').evidence,
  analyst: out('Validate Evidence Coverage').artifact,
  strategy: out('Validate Strategy Grounding').strategy,
}));
fs.writeFileSync(`${base}.md`, `<!-- n8n execution ${id}, run ${clientRunId}: the Google Doc as n8n read it back after writing -->\n`
  + lines.join('\n') + '\n');
console.log(`exported execution ${id} -> ${path.relative(process.cwd(), base)}.json / .md`);
