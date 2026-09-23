"""Both implementations' run_records against schemas/run_record.schema.json.
The n8n builder is exercised through node, so one test run checks both sides."""

import json
import shutil
import subprocess
from pathlib import Path

import jsonschema
import pytest

from wire3_gtm.run_record import backfill, brief_id, build, events_by_run

REPO = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((REPO / "schemas" / "run_record.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)
N8N_LOG, CREWAI_LOG = REPO / "n8n" / "logs" / "runs.jsonl", REPO / "crewai" / "logs" / "runs.jsonl"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def errors(rec):
    return [f"{'/'.join(map(str, e.absolute_path))}: {e.message}" for e in VALIDATOR.iter_errors(rec)]


def node(script: str) -> str:
    return subprocess.run(["node", "-e", script], cwd=REPO / "n8n", check=True, capture_output=True, text=True).stdout


def finished_runs(log: Path):
    return {cid: ev for cid, ev in events_by_run(log.read_text()).items()
            if any(e["event_type"] == "run_complete" for e in ev)}


@pytest.mark.parametrize("cid", sorted(finished_runs(CREWAI_LOG)))
def test_crewai_builder_matches_the_schema_for_every_logged_run(cid):
    rec = build(finished_runs(CREWAI_LOG)[cid], {"company": "Wire3"})
    assert errors(rec) == []
    assert rec["run_id"] == cid and rec["implementation"] == "crewai"


@needs_node
def test_n8n_builder_matches_the_schema_for_every_logged_run():
    out = node("""const r=require('./scripts/run_record');const fs=require('fs');
      const runs=r.eventsByRun(fs.readFileSync('logs/runs.jsonl','utf8')); const recs=[];
      for (const ev of Object.values(runs)) if (ev.some(e=>e.event_type==='run_complete')) recs.push(r.build(ev,{company:'Wire3'}));
      console.log(JSON.stringify(recs));""")
    recs = json.loads(out)
    assert recs and all(errors(r) == [] for r in recs), [errors(r) for r in recs]
    # a null n8n cannot measure is declared, not silently missing
    assert all(r["links"]["broken"] is None and "links.broken" in r["not_measured"] for r in recs)


@needs_node
def test_brief_id_is_the_same_in_python_and_javascript():
    for brief in (json.loads((REPO / "brief.json").read_text()),
                  {"b": [1, 2.5, None, True], "a": {"z": "Ocala — FL ✓", "y": []}}):
        js = node(f"console.log(require('./scripts/run_record').briefId({json.dumps(brief)}))").strip()
        assert js == brief_id(brief)


def test_every_run_record_already_logged_is_valid():
    for log in (N8N_LOG, CREWAI_LOG):
        for line in log.read_text().splitlines():
            if '"event_type": "run_record"' in line or '"event_type":"run_record"' in line:
                assert errors(json.loads(line)) == []


def test_backfill_adds_one_record_per_finished_run_and_is_idempotent(tmp_path):
    log = tmp_path / "runs.jsonl"
    shutil.copy(CREWAI_LOG, log)
    before = [c for c, ev in finished_runs(log).items() if not any(e["event_type"] == "run_record" for e in ev)]
    assert backfill(log, lambda cid: None) == before
    assert backfill(log, lambda cid: None) == []


def test_a_failed_run_keeps_its_error_and_failed_step():
    failed = next(ev for ev in finished_runs(CREWAI_LOG).values()
                  if next(e for e in ev if e["event_type"] == "run_complete")["status"] == "failed")
    rec = build(failed, None)
    assert rec["status"] == "failed" and rec["failed_step"] and rec["errors"]
    assert rec["brief_id"] is None and errors(rec) == []


def test_both_implementations_read_the_one_shared_brief():
    from wire3_gtm.pipeline import BRIEF_PATH

    assert BRIEF_PATH == REPO / "brief.json"
    script = (REPO / "n8n" / "scripts" / "run_pipeline.js").read_text()
    assert "path.resolve(__dirname, '..', '..', 'brief.json')" in script
    assert not (REPO / "crewai" / "brief.json").exists()


@needs_node
def test_n8n_evidence_gate_repairs_invented_ids():
    """Runs the deployed n8n gate code (from the workflow JSON) against execution 28's real Analyst output."""
    r = subprocess.run(["node", "scripts/test_evidence_gate.js"], cwd=REPO / "n8n", capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
