// n8n Code node: "Build Docs Content" (Docs Writer, step 1 of 5)
// Renders the accepted Strategy artifact into Google Docs batchUpdate requests.
// Source of truth for the node body -- paste into the Code node's JS field.
const fs = require('fs');
const LOG_DIR = './logs';
function appendLog(event) {
  fs.mkdirSync(LOG_DIR, { recursive: true });
  fs.appendFileSync(LOG_DIR + '/runs.jsonl', JSON.stringify({ ts: new Date().toISOString(), implementation: 'n8n', ...event }) + '\n');
}

const input = $input.first().json;
const clientRunId = input._client_run_id;
const runId = input._run_id;
const startedAt = input._t_docs_writer_start;

function fail(message) {
  appendLog({
    event_type: 'document_write',
    client_run_id: clientRunId,
    run_id: runId,
    node: 'Build Docs Content',
    status: 'error',
    duration_ms: startedAt ? Date.now() - new Date(startedAt).getTime() : null,
    error: message,
  });
  throw new Error('Docs Writer: ' + message);
}

if (input.status !== 'accepted') {
  fail('received an item that was not marked accepted by the validation gate -- refusing to proceed.');
}

const artifact = input.artifact;
const analyst = $('Validate Evidence Coverage').first().json.artifact || {};
const evidenceSet = $('Build Evidence Records').first().json.evidence || [];
const evById = new Map(evidenceSet.map((e) => [e.evidence_id, e]));

// ---- 1. Index analyst-level findings so THEME-/SWOT-/ASM-/UNK-/CONF- ids resolve to EV- ids ----
const findings = new Map(); // id -> { id, label, evidence_ids }
const evIdsOf = (x) => x.evidence_ids || x.supporting_evidence_ids || x.related_evidence_ids || [];
const addFinding = (id, label, evs) => {
  if (id) findings.set(id, { id, label: String(label || id), evidence_ids: Array.isArray(evs) ? evs : [] });
};
for (const t of analyst.market_themes || []) addFinding(t.theme_id, t.title, evIdsOf(t));
const swot = analyst.swot || {};
for (const group of ['strengths', 'weaknesses', 'opportunities', 'threats']) {
  for (const s of swot[group] || []) addFinding(s.item_id, s.statement, evIdsOf(s));
}
const auc = analyst.assumptions_unknowns_conflicts || {};
for (const group of ['assumptions', 'unknowns', 'conflicts']) {
  for (const x of auc[group] || []) addFinding(x.id, x.statement || x.description || x.title, evIdsOf(x));
}

// ---- 2. Collect every supporting_ids reference in the Strategy artifact ----
const isCited = (v) => v && typeof v === 'object' && !Array.isArray(v) && Array.isArray(v.supporting_ids) && 'value' in v;
const citedIds = new Set();
(function walk(o) {
  if (Array.isArray(o)) return o.forEach(walk);
  if (o && typeof o === 'object') {
    if (Array.isArray(o.supporting_ids)) o.supporting_ids.forEach((id) => citedIds.add(id));
    Object.values(o).forEach(walk);
  }
})(artifact);

const directEv = new Set();
const citedFindings = [];
const unresolved = [];
for (const id of citedIds) {
  if (/^EV-/.test(id)) directEv.add(id);
  else if (findings.has(id)) citedFindings.push(findings.get(id));
  else unresolved.push(id);
}
if (unresolved.length) fail(`Unresolvable citation id(s): ${unresolved.join(', ')}`);

const sourceIds = new Set(directEv);
for (const f of citedFindings) f.evidence_ids.forEach((id) => sourceIds.add(id));
const orphaned = [...sourceIds].filter((id) => !evById.has(id));
if (orphaned.length) fail(`Orphaned citation(s) -- evidence_id not in the evidence set: ${orphaned.join(', ')}`);

const natural = (a, b) => String(a).localeCompare(String(b), 'en', { numeric: true });
citedFindings.sort((a, b) => natural(a.id, b.id));
const sources = [...sourceIds].sort(natural).map((id) => evById.get(id));

// ---- 3. Build blocks: { text, style, boldLen, links[] } ----
const blocks = [];
const add = (text, style = 'P', extra = {}) => blocks.push({ text, style, ...extra });
const label = (k) => k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
const cite = (f) => {
  const ids = f.supporting_ids || [];
  const tag = ids.length ? ids.join(', ') : f.basis === 'brief_stated' ? 'brief' : f.basis || 'inference';
  const v = Array.isArray(f.value) ? f.value.join(', ') : String(f.value);
  return `${v} [${tag}]`;
};
const primitive = (v) => (Array.isArray(v) ? v.join(', ') : String(v));
const labelled = (k, text, style = 'BULLET') => {
  const lead = `${label(k)}: `;
  add(lead + text, style, { boldLen: lead.length });
};

function renderObject(obj, skip = []) {
  for (const [k, v] of Object.entries(obj)) {
    if (skip.includes(k) || v === null || v === undefined) continue;
    if (isCited(v)) labelled(k, cite(v));
    else if (Array.isArray(v) && v.length && v.every(isCited)) {
      add(`${label(k)}:`, 'BULLET', { boldLen: label(k).length + 1 });
      v.forEach((c) => add(cite(c), 'BULLET2'));
    } else if (Array.isArray(v) && v.every((x) => x === null || typeof x !== 'object')) labelled(k, primitive(v));
    else if (Array.isArray(v)) {
      add(`${label(k)}:`, 'BULLET', { boldLen: label(k).length + 1 });
      v.forEach((item) => (item && typeof item === 'object' ? renderObject(item) : add(primitive(item), 'BULLET2')));
    } else if (typeof v === 'object') {
      add(`${label(k)}:`, 'BULLET', { boldLen: label(k).length + 1 });
      renderObject(v);
    } else labelled(k, primitive(v));
  }
}

function renderItems(items, idKey, nameKey) {
  for (const item of items || []) {
    const name = nameKey && item[nameKey] ? (isCited(item[nameKey]) ? cite(item[nameKey]) : item[nameKey]) : '';
    add(`${item[idKey]}${name ? ': ' + name : ''}`, 'H3');
    renderObject(item, [idKey, nameKey].filter(Boolean));
  }
}

const dateStr = new Date().toISOString().slice(0, 16).replace('T', ' ');
const title = `Wire3 GTM Plan - ${artifact.brief_id} - ${dateStr} UTC`;

add('Wire3 Go-To-Market Plan', 'TITLE');
add(`Brief: ${artifact.brief_id} | Generated: ${dateStr} UTC | Sources cited: ${sources.length}`, 'P');
add(
  'How to read citations: bracketed IDs such as [THEME-3] are analyst findings, listed in Appendix A with the evidence behind them; every source is in Appendix B as a clickable link. [inference] marks strategist judgment not directly supported by a source; [brief] marks a fact stated in the project brief.',
  'P',
);

const sections = [];
const section = (name, fn) => { sections.push(name); add(`${sections.length}. ${name}`, 'H2'); fn(); };

section('Ideal customer profiles', () => renderItems(artifact.icps, 'icp_id', 'name'));
section('Customer pains and desired outcomes', () => renderItems(artifact.pains_and_outcomes, 'pain_id'));
section('Value proposition', () => renderObject(artifact.value_proposition));
section('Positioning and differentiation', () => renderObject(artifact.positioning));
section('Message pillars', () => renderItems(artifact.message_pillars, 'pillar_id', 'title'));
section('Recommended channels', () => renderItems(artifact.channels, 'channel_id', 'channel_name'));
section('Launch phases and activities', () => {
  const phases = [...(artifact.launch_phases || [])].sort((a, b) => (a.sequence_order || 0) - (b.sequence_order || 0));
  renderItems(phases, 'phase_id', 'phase_name');
});
section('Success metrics', () => renderItems(artifact.success_metrics, 'metric_id', 'name'));
section('Risks', () => renderItems(artifact.risks, 'risk_id'));
section('Follow-up research questions', () => renderItems(artifact.follow_up_research_questions, 'frq_id'));

add('Appendix A: Analyst findings referenced', 'H2');
for (const f of citedFindings) {
  const lead = `[${f.id}] `;
  const evs = f.evidence_ids.length ? f.evidence_ids.map((e) => `[${e}]`).join(' ') : 'no direct source evidence (assumption or open unknown)';
  add(`${lead}${f.label} -- ${evs}`, 'BULLET', { boldLen: lead.length });
}

add('Appendix B: Sources', 'H2');
const HTTP_URL_RE = /^https?:\/\/\S+$/i;
for (const e of sources) {
  const lead = `[${e.evidence_id}] `;
  const t = e.source_title || e.source_url || e.evidence_id;
  const ok = HTTP_URL_RE.test(e.source_url || '');
  const tail = ` (${e.source_type || 'source'}${e.publication_date ? ', ' + e.publication_date : ''})`;
  add(lead + t + tail, 'BULLET', { boldLen: lead.length, link: ok ? { offset: lead.length, length: t.length, url: e.source_url } : null });
}

// ---- 4. Lay out text and translate blocks into Docs API requests ----
let pos = 1; // Docs body index starts at 1
let fullText = '';
for (const b of blocks) {
  b.start = pos;
  fullText += b.text + '\n';
  pos += b.text.length + 1;
  b.end = pos;
}

const requests = [{ insertText: { location: { index: 1 }, text: fullText } }];
const styleMap = { TITLE: 'TITLE', H2: 'HEADING_2', H3: 'HEADING_3', P: 'NORMAL_TEXT', BULLET: 'NORMAL_TEXT', BULLET2: 'NORMAL_TEXT' };
for (const b of blocks) {
  requests.push({
    updateParagraphStyle: {
      range: { startIndex: b.start, endIndex: b.end },
      paragraphStyle: { namedStyleType: styleMap[b.style] },
      fields: 'namedStyleType',
    },
  });
}
// Bold lead labels and hyperlinks (text-only styles, so no index shifting).
for (const b of blocks) {
  if (b.boldLen) {
    requests.push({ updateTextStyle: { range: { startIndex: b.start, endIndex: b.start + b.boldLen }, textStyle: { bold: true }, fields: 'bold' } });
  }
  if (b.link) {
    requests.push({
      updateTextStyle: {
        range: { startIndex: b.start + b.link.offset, endIndex: b.start + b.link.offset + b.link.length },
        textStyle: { link: { url: b.link.url } },
        fields: 'link',
      },
    });
  }
}
// Bullets last: merge consecutive bullet paragraphs into one range each.
let run = null;
const flush = () => {
  if (run) requests.push({ createParagraphBullets: { range: { startIndex: run.start, endIndex: run.end }, bulletPreset: 'BULLET_DISC_CIRCLE_SQUARE' } });
  run = null;
};
for (const b of blocks) {
  if (b.style === 'BULLET' || b.style === 'BULLET2') run = run ? { start: run.start, end: b.end } : { start: b.start, end: b.end };
  else flush();
}
flush();

const expected = {
  title,
  section_headings: sections.map((s, i) => `${i + 1}. ${s}`),
  source_ids: sources.map((e) => e.evidence_id),
  link_urls: sources.filter((e) => HTTP_URL_RE.test(e.source_url || '')).map((e) => e.source_url),
  allowed_evidence_ids: evidenceSet.map((e) => e.evidence_id),
};

appendLog({
  event_type: 'document_write',
  client_run_id: clientRunId,
  run_id: runId,
  node: 'Build Docs Content',
  status: 'content_built',
  duration_ms: startedAt ? Date.now() - new Date(startedAt).getTime() : null,
  section_count: sections.length,
  source_count: sources.length,
  cited_finding_count: citedFindings.length,
  request_count: requests.length,
});

return [{
  json: {
    title,
    requests,
    expected,
    brief_id: artifact.brief_id,
    artifact,
    _client_run_id: clientRunId,
    _run_id: runId,
    _t_docs_writer_start: startedAt,
  },
}];
