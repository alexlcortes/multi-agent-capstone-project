import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError
from test_analyst_models import EV, valid  # noqa: F401  (Analyst fixture reused)

from wire3_gtm.analyst_models import AnalystArtifact, dump_contract
from wire3_gtm.strategy_checks import analyst_ids, grounding_errors, report, unknown_coverage_errors
from wire3_gtm.strategy_models import StrategyArtifact

SCHEMA = json.loads((Path(__file__).parents[2] / "schemas/strategy_artifact.schema.json").read_text())


def c(value, basis="inference", ids=None):
    return {"value": value, "supporting_ids": ids or [], "basis": basis}


@pytest.fixture
def strategy():
    return {
        "brief_id": "run-1",
        "icps": [{"icp_id": "ICP-1", "name": "Promo-cliff switcher", "description": c("d"),
                  "current_provider": c(["Spectrum"], "evidence", ["THEME-1"])}],
        "pains_and_outcomes": [{"pain_id": "PAIN-1", "pain_statement": c("bill shock", "evidence", ["THEME-1"]),
                                "desired_outcome": c("flat price"), "related_icp_ids": ["ICP-1"], "severity": c("high")}],
        "value_proposition": {"headline": c("h"), "supporting_points": [c("p", "evidence", [EV[0]])],
                              "no_bundle_gap_response": c("r", "brief_stated"),
                              "low_brand_recognition_response": c("r", "brief_stated")},
        "positioning": {"positioning_statement": c("s"), "differentiators": [c("d")], "competitive_frame": ["Spectrum"]},
        "message_pillars": [{"pillar_id": f"PILLAR-{i}", "title": "t", "supporting_message": c("m"),
                             "target_icp_ids": ["ICP-1"]} for i in (1, 2, 3)],
        "channels": [{"channel_id": "CHANNEL-1", "channel_name": "local groups", "channel_category": "digital_organic",
                      "budget_tier": "low", "rationale": c("cheap"), "target_icp_ids": ["ICP-1"]}],
        "launch_phases": [{"phase_id": "PHASE-1", "phase_name": "p", "sequence_order": 1, "timeframe": "Weeks 1-4",
                           "activities": [c("a")], "exit_criteria": "e"}],
        "success_metrics": [{"metric_id": "METRIC-1", "name": "n", "definition": "d", "target_value": c(100.0),
                             "measurement_cadence": "monthly"}],
        "risks": [{"risk_id": "RISK-1", "statement": c("r"), "likelihood": c("low"), "impact": c("high"), "mitigation": c("m")}],
        "follow_up_research_questions": [],
    }


def test_valid_strategy_passes_pydantic_and_original_json_schema(strategy):
    from wire3_gtm.analyst_models import dump_contract as dump
    jsonschema.validate(dump(StrategyArtifact.model_validate(strategy)), SCHEMA)


def test_evidence_basis_needs_supporting_ids(strategy):
    strategy["icps"][0]["description"] = c("d", "evidence", [])
    with pytest.raises(ValidationError, match="requires non-empty supporting_ids"):
        StrategyArtifact.model_validate(strategy)


def test_own_output_ids_can_never_be_supporting_ids(strategy):
    strategy["message_pillars"][0]["supporting_message"] = c("m", "evidence", ["PILLAR-2"])
    with pytest.raises(ValidationError):
        StrategyArtifact.model_validate(strategy)


def test_both_gap_responses_are_required(strategy):
    del strategy["value_proposition"]["no_bundle_gap_response"]
    with pytest.raises(ValidationError):
        StrategyArtifact.model_validate(strategy)


def test_pillar_and_icp_bounds(strategy):
    strategy["message_pillars"] = strategy["message_pillars"][:2]
    with pytest.raises(ValidationError):
        StrategyArtifact.model_validate(strategy)


def test_dangling_icp_reference_is_rejected(strategy):
    strategy["channels"][0]["target_icp_ids"] = ["ICP-9"]
    with pytest.raises(ValidationError, match="ICPs that do not exist"):
        StrategyArtifact.model_validate(strategy)


def test_supporting_ids_must_exist_in_the_analyst_artifact(strategy, valid):
    analyst = AnalystArtifact.model_validate(valid)
    assert {"THEME-1", "SWOT-S1"} <= analyst_ids(analyst)["THEME"] | analyst_ids(analyst)["SWOT"]
    good = StrategyArtifact.model_validate(strategy)
    assert grounding_errors(good, analyst) == []
    strategy["icps"][0]["current_provider"] = c(["Spectrum"], "evidence", ["THEME-9"])
    strategy["value_proposition"]["supporting_points"] = [c("p", "evidence", ["EV-dddddddd"])]
    errs = grounding_errors(StrategyArtifact.model_validate(strategy), analyst)
    assert len(errs) == 2 and "THEME-9" in errs[0] and "EV-dddddddd" in errs[1]


def test_every_analyst_unknown_needs_a_follow_up_question(strategy, valid):
    valid["assumptions_unknowns_conflicts"]["unknowns"] = [
        {"id": "UNK-1", "research_question_id": "RQ8", "description": "d", "reason_unresolved": "r"}]
    analyst = AnalystArtifact.model_validate(valid)
    assert "UNK-1" in unknown_coverage_errors(StrategyArtifact.model_validate(strategy), analyst)[0]
    strategy["follow_up_research_questions"] = [
        {"frq_id": "FRQ-1", "question": "q", "rationale": "r", "related_unknown_ids": ["UNK-1"], "priority": "high"}]
    assert unknown_coverage_errors(StrategyArtifact.model_validate(strategy), analyst) == []


def test_report_flags_wire3_response_claiming_evidence(strategy):
    strategy["value_proposition"]["no_bundle_gap_response"] = c("r", "evidence", ["THEME-1"])
    r = report(StrategyArtifact.model_validate(strategy))
    assert r["wire3_response_claims_evidence"] == ["no_bundle_gap_response"]
