// n8n Code node: "Verify Document & Log" (Docs Writer, step 5 of 5)
// Reads back the created Google Doc and checks it matches what Build Docs Content planned.
// Source of truth for the node body -- paste into the Code node's JS field.
const fs = require('fs');
const LOG_DIR = './logs';
function appendLog(event) {
  fs.mkdirSync(LOG_DIR, { recursive: true });
  fs.appendFileSync(LOG_DIR + '/runs.jsonl', JSON.stringify({ ts: new Date().toISOString(), implementation: 'n8n', ...event }) + '\n');
}

const build = $('Build Docs Content').first().json;
const created = $('Docs: Create Document').first().json;
const doc = $input.first().json;
const startedAt = build._t_docs_writer_start;
const expected = build.expected;

const documentId = created.documentId || doc.documentId;
const documentUrl = `https://docs.google.com/document/d/${documentId}/edit`;

// Flatten the doc into plain text and a set of link URLs.
let text = '';
const linkUrls = new Set();
for (const block of (doc.body && doc.body.content) || []) {
  for (const el of (block.paragraph && block.paragraph.elements) || []) {
    const run = el.textRun;
    if (!run) continue;
    text += run.content || '';
    const url = run.textStyle && run.textStyle.link && run.textStyle.link.url;
    if (url) linkUrls.add(url);
  }
}

const errors = [];
if (doc.title !== expected.title) errors.push(`Title mismatch: expected "${expected.title}", got "${doc.title}".`);
for (const h of expected.section_headings) if (!text.includes(h)) errors.push(`Missing section heading: ${h}`);
for (const id of expected.source_ids) if (!text.includes(`[${id}]`)) errors.push(`Source ${id} missing from Appendix B.`);
for (const url of expected.link_urls) if (!linkUrls.has(url)) errors.push(`Missing hyperlink for source URL: ${url}`);

// Every evidence_id referenced anywhere in the document must exist in the evidence set (orphan check).
const allowed = new Set(expected.allowed_evidence_ids);
const referenced = new Set(text.match(/\bEV-[0-9a-f]{8}\b/g) || []);
const orphaned = [...referenced].filter((id) => !allowed.has(id));
if (orphaned.length) errors.push(`Orphaned evidence_id(s) in document: ${orphaned.join(', ')}`);

const durationMs = startedAt ? Date.now() - new Date(startedAt).getTime() : null;
appendLog({
  event_type: 'document_write',
  client_run_id: build._client_run_id,
  run_id: build._run_id,
  node: 'Verify Document & Log',
  status: errors.length ? 'error' : 'ok',
  duration_ms: durationMs,
  document_id: documentId,
  document_url: documentUrl,
  section_count: expected.section_headings.length,
  source_count: expected.source_ids.length,
  link_count: linkUrls.size,
  referenced_evidence_ids: referenced.size,
  orphaned_evidence_ids: orphaned.length,
  errors,
});
if (errors.length) throw new Error('Docs Writer post-write check failed: ' + errors.join(' | '));

const artifact = build.artifact;
return [{
  json: {
    docs_writer_status: 'document_created',
    document_id: documentId,
    document_url: documentUrl,
    checks: {
      sections_present: expected.section_headings.length,
      sources_listed: expected.source_ids.length,
      hyperlinks_verified: expected.link_urls.length,
      orphaned_evidence_ids: 0,
    },
    artifact,
    _client_run_id: build._client_run_id,
    _run_id: build._run_id,
  },
}];
