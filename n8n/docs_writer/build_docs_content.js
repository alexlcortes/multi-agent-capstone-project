// n8n Code node: "Build Docs Content" (Docs Writer, step 1 of 5)
// Renders the Analyst and Strategy artifacts into Google Docs batchUpdate requests, in the same section
// layout as the CrewAI Docs Writer (schemas/gtm_document_template.md, rule 4: n8n parity).
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
// A cited field: Strategy fields carry supporting_ids, Analyst fields carry evidence_ids.
const isCited = (v) => v && typeof v === 'object' && !Array.isArray(v) && 'value' in v && 'basis' in v
  && (Array.isArray(v.supporting_ids) || Array.isArray(v.evidence_ids));
const isDerived = (v) => v && typeof v === 'object' && !Array.isArray(v) && Array.isArray(v.derived_from_evidence_ids);
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
  const ids = f.supporting_ids || f.evidence_ids || [];
  const tag = f.basis === 'inference' && ids.length ? `inference; ${ids.join(', ')}` : ids.length ? ids.join(', ') : f.basis === 'brief_stated' ? 'brief' : f.basis || 'inference';
  return `${primitive(f.value)} [${tag}]`;
};
const primitive = (v) => (v === null || v === undefined ? 'n/a' : typeof v === 'boolean' ? (v ? 'Yes' : 'No')
  : Array.isArray(v) ? v.map(primitive).join(', ') : String(v));
const labelled = (k, text, style = 'BULLET') => {
  const lead = `${label(k)}: `;
  add(lead + text, style, { boldLen: lead.length });
};

function renderObject(obj, skip = []) {
  for (const [k, v] of Object.entries(obj)) {
    if (skip.includes(k) || v === null || v === undefined) continue;
    if (isCited(v)) labelled(k, cite(v));
    else if (isDerived(v)) labelled(k, `${primitive(v.value)} [${v.derived_from_evidence_ids.join(', ')}] (computed: ${v.formula})`);
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
const headerAt = blocks.length;
add('', 'P'); // header line, filled in once the source count is known
add(
  'How to read citations: bracketed IDs such as [THEME-3] are analyst findings, listed in Appendix A with the evidence behind them; every source is in Appendix B as a clickable link. [inference] marks strategist judgment not directly supported by a source; [brief] marks a fact stated in the project brief.',
  'P',
);

const sections = [];
const section = (name, fn) => { sections.push(name); add(`${sections.length}. ${name}`, 'H2'); fn(); };
let plan = {};
try { plan = $('Head Planner').first().json.output || {}; } catch (e) { plan = {}; }

section('Executive summary', () => {
  const vp = artifact.value_proposition || {}, pos = artifact.positioning || {};
  if (plan.segment || plan.region) add(`Target: ${plan.segment || 'n/a'} in ${plan.region || 'n/a'}.`, 'P');
  if (vp.headline) labelled('value_proposition', cite(vp.headline), 'P');
  if (pos.positioning_statement) labelled('positioning', cite(pos.positioning_statement), 'P');
  const ch = artifact.channels || [];
  add(`The plan recommends ${ch.length} channels (${ch.map((c) => c.channel_name).join(', ')}), `
    + `${(artifact.launch_phases || []).length} launch phases and ${(artifact.success_metrics || []).length} success metrics, and lists `
    + `${(artifact.risks || []).length} risks and ${(artifact.follow_up_research_questions || []).length} follow-up research questions.`, 'P');
});
section('Research scope and evidence', () => {
  const dates = evidenceSet.map((e) => String(e.retrieval_timestamp || '').slice(0, 10)).filter(Boolean).sort();
  const range = dates.length ? ` retrieved ${dates[0]}${dates[dates.length - 1] !== dates[0] ? ' to ' + dates[dates.length - 1] : ''}` : '';
  add(`${(plan.planned_tool_calls || []).length} planned research calls returned ${evidenceSet.length} evidence records${range}. Research questions:`, 'P');
  for (const q of plan.research_questions || []) {
    const lead = `${q.id}: `;
    add(lead + q.question, 'BULLET', { boldLen: lead.length });
  }
});
section('Competitor comparison', () => (analyst.competitor_comparison_table || []).forEach((r) => {
  add(r.competitor_name, 'H3'); renderObject(r, ['competitor_name']);
}));
section('Product and feature comparison', () => (analyst.product_feature_comparison || []).forEach((r) => {
  add(r.competitor_name, 'H3'); renderObject(r, ['competitor_name']);
}));
section('Pricing matrix', () => (analyst.pricing_matrix || []).forEach((r) => {
  add(`${r.competitor_name}: ${isCited(r.plan_name) ? r.plan_name.value : r.plan_name}`, 'H3');
  renderObject(r, ['competitor_name']); // plan_name stays in the body so its citations are shown
}));
section('Market themes', () => (analyst.market_themes || []).forEach((t) => {
  add(`${t.theme_id}: ${t.title}`, 'H3');
  renderObject({ description: t.description, related_research_questions: t.related_research_question_ids || [],
    competitors_involved: t.competitors_involved || [], evidence: t.supporting_evidence_ids || [] });
}));
section('SWOT analysis', () => {
  for (const group of ['strengths', 'weaknesses', 'opportunities', 'threats']) {
    add(label(group), 'H3');
    for (const x of swot[group] || []) {
      const ids = x.evidence_ids || [];
      const tag = ids.length ? ids.join(', ') : x.basis === 'brief_stated' ? 'brief' : x.basis || 'inference';
      const lead = `${x.item_id}: `;
      add(`${lead}${x.statement} [${tag}]`, 'BULLET', { boldLen: lead.length });
    }
  }
});
section('7P market analysis', () => Object.entries(analyst.seven_p_analysis || {}).forEach(([k, v]) => {
  add(label(k), 'H3'); renderObject(v || {});
}));
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
section('Assumptions, unknowns and conflicts', () => {
  for (const [title, key] of [['Assumptions', 'assumptions'], ['Unknowns', 'unknowns'], ['Conflicts', 'conflicts']]) {
    add(title, 'H3');
    if (!(auc[key] || []).length) add('None recorded.', 'P');
    for (const x of auc[key] || []) {
      add(x.id, 'P', { boldLen: String(x.id).length });
      const { id, ...rest } = x;
      renderObject(rest);
    }
  }
});
section('Follow-up research questions', () => renderItems(artifact.follow_up_research_questions, 'frq_id'));

add('Appendix A: Analyst findings referenced', 'H2');
for (const f of citedFindings) {
  const lead = `[${f.id}] `;
  const evs = f.evidence_ids.length ? f.evidence_ids.map((e) => `[${e}]`).join(' ') : 'no direct source evidence (assumption or open unknown)';
  add(`${lead}${f.label} -- ${evs}`, 'BULLET', { boldLen: lead.length });
}

const EV_IN_TEXT = /\bEV-[0-9a-f]{8}\b/g;
for (const b of blocks) for (const m of b.text.match(EV_IN_TEXT) || []) sourceIds.add(m);
const orphanedInText = [...sourceIds].filter((id) => !evById.has(id));
if (orphanedInText.length) fail(`Orphaned citation(s) -- evidence_id not in the evidence set: ${orphanedInText.join(', ')}`);
sources.splice(0, sources.length, ...[...sourceIds].sort(natural).map((id) => evById.get(id)));
blocks[headerAt].text = `Run: ${runId} | Brief: ${artifact.brief_id} | Generated: ${dateStr} UTC | Sources cited: ${sources.length}`;

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
