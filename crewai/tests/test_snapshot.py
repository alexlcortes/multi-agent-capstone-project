import json

import pytest
from test_analyst_models import EV, valid  # noqa: F401
from test_run_store import EVIDENCE, PLAN
from test_strategy import strategy  # noqa: F401

from wire3_gtm.docs_content import build_document, to_docs_requests
from wire3_gtm.docs_google import simulate_docs, verify
from wire3_gtm.evidence import EvidenceSet
from wire3_gtm.run_store import RunStore
from wire3_gtm.snapshot import SNAPSHOTS_DIR, SnapshotError, export_snapshot, load_snapshot


def finished_run(tmp_path, analyst, strategy):
    store = RunStore("run-snap", root=tmp_path / "runs")
    store.save_text("01_plan.json", PLAN.model_dump_json())
    store.save_text("02_evidence_set.json", EvidenceSet(run_id="plan-1", evidence=EVIDENCE).model_dump_json())
    store.save_json("03_analyst_artifact.json", analyst)
    store.save_json("04_strategy_artifact.json", strategy)
    return store


def test_export_writes_every_artifact_markdown_and_checksums(tmp_path, valid, strategy):
    out = export_snapshot(finished_run(tmp_path, valid, strategy), tmp_path / "snap")
    meta = json.loads((out / "snapshot.json").read_text())
    assert meta["source_run_id"] == "run-snap" and meta["brief_id"] == strategy["brief_id"]
    assert set(meta["sha256"]) == {"01_plan.json", "02_evidence_set.json", "03_analyst_artifact.json",
                                   "04_strategy_artifact.json", "strategy.md"}
    md = (out / "strategy.md").read_text()
    for icp in strategy["icps"]:
        assert icp["icp_id"] in md
    assert "## Cited ids" in md and "[THEME-1]" in md
    snap = load_snapshot(out)
    assert snap.strategy.brief_id == strategy["brief_id"] and len(snap.evidence.evidence) == len(EVIDENCE)


def test_refuses_a_strategy_citing_an_id_the_analyst_does_not_have(tmp_path, valid, strategy):
    strategy["icps"][0]["current_provider"]["supporting_ids"] = ["THEME-99"]
    with pytest.raises(SnapshotError, match="THEME-99"):
        export_snapshot(finished_run(tmp_path, valid, strategy), tmp_path / "snap")
    assert not (tmp_path / "snap").exists()


def test_refuses_an_incomplete_run(tmp_path):
    with pytest.raises(SnapshotError, match="did not finish the Strategy step"):
        export_snapshot(RunStore("run-empty", root=tmp_path), tmp_path / "snap")


def test_load_refuses_an_edited_file(tmp_path, valid, strategy):
    out = export_snapshot(finished_run(tmp_path, valid, strategy), tmp_path / "snap")
    path = out / "04_strategy_artifact.json"
    path.write_text(path.read_text().replace("Promo-cliff switcher", "Edited by hand"))
    with pytest.raises(SnapshotError, match="04_strategy_artifact.json"):
        load_snapshot(out)


def test_a_three_row_pricing_matrix_is_a_recorded_deviation_not_a_failure(tmp_path, valid, strategy):
    valid["pricing_matrix"] = [r for r in valid["pricing_matrix"] if r["competitor_name"] != "Wire3"][:3]
    assert len(valid["pricing_matrix"]) == 3
    out = export_snapshot(finished_run(tmp_path, valid, strategy), tmp_path / "snap")
    deviations = json.loads((out / "snapshot.json").read_text())["known_schema_deviations"]
    assert len(deviations) == 1 and "Decision 2" in deviations[0]


def test_other_schema_violations_still_fail(tmp_path, valid, strategy):
    valid["market_themes"][0]["title"] = "x" * 200  # schema maxLength, not a known deviation
    with pytest.raises(SnapshotError):
        export_snapshot(finished_run(tmp_path, valid, strategy), tmp_path / "snap")


COMMITTED = sorted(p for p in SNAPSHOTS_DIR.glob("*") if (p / "snapshot.json").exists())


@pytest.mark.parametrize("path", COMMITTED, ids=[p.name for p in COMMITTED])
def test_committed_snapshot_is_intact_and_builds_a_verified_document(path):
    """The point of a snapshot: the Docs Writer runs on it offline, no Google."""
    snap = load_snapshot(path)
    doc = build_document(snap.strategy, snap.analyst, snap.evidence.evidence, snap.plan,
                         snap.meta["source_run_id"], analyst_report=snap.analyst_report)
    requests = to_docs_requests(doc)
    assert verify(doc.expected, simulate_docs(doc, requests)) == []
