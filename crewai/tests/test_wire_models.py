import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_analyst_models import cited, valid  # noqa: F401
from test_run_store import PLAN
from test_strategy import strategy  # noqa: F401

from wire3_gtm.analyst_checks import make_analyst_guardrail
from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.evidence import EvidenceRecord
from wire3_gtm.models import ResearchPlan, plan_guardrail
from wire3_gtm.strategy_checks import make_strategy_guardrail
from wire3_gtm.strategy_models import StrategyArtifact
from wire3_gtm.tasks import OUTPUT_MODELS
from wire3_gtm.wire_models import wire_model

EVIDENCE = [EvidenceRecord(evidence_id="EV-aaaaaaaa", research_question_id="RQ2", claim="c", source_title="t",
                           source_url="https://x.example", source_type="company",
                           retrieval_timestamp="2026-09-20T00:00:00Z", excerpt="e")]


def draft(valid):
    """The reply that killed a live run: a short promo with no sourced post-promo price."""
    valid["pricing_matrix"][0]["promo_duration_months"] = cited(6)
    valid["pricing_matrix"][0]["post_promo_price_usd_per_month"] = cited(None, "inference")
    return valid


def as_output(d):
    return SimpleNamespace(raw=json.dumps(d), pydantic=None)


def test_the_sdk_facing_model_accepts_a_draft_the_strict_model_rejects(valid):
    d = draft(valid)
    wire_model(AnalystArtifact).model_validate(d)  # what chat.completions.parse now sees: no exception
    with pytest.raises(ValidationError, match="shorter than 12 months"):
        AnalystArtifact.model_validate(d)  # what used to raise inside the SDK and kill the step


def test_wire_models_keep_the_structural_constraints(valid):
    valid["competitor_comparison_table"] = valid["competitor_comparison_table"][:3]  # min/max 4 items
    with pytest.raises(ValidationError):
        wire_model(AnalystArtifact).model_validate(valid)


def test_the_schema_sent_to_openai_is_unchanged():
    """Validators do not appear in JSON Schema, so the wire twin must describe exactly the same contract."""
    for strict in (AnalystArtifact, StrategyArtifact, ResearchPlan):
        assert wire_model(strict).model_json_schema() == strict.model_json_schema()


def test_tasks_hand_the_llm_the_wire_models_not_the_strict_ones():
    for name, strict in (("analyze_evidence", AnalystArtifact), ("build_strategy", StrategyArtifact),
                         ("plan_research", ResearchPlan)):
        assert OUTPUT_MODELS[name] is not strict and OUTPUT_MODELS[name].__name__ == strict.__name__


def test_analyst_guardrail_turns_a_rule_violation_into_retry_feedback_not_an_exception(valid):
    guardrail = make_analyst_guardrail(EVIDENCE)
    ok, feedback = guardrail(as_output(draft(valid)))
    assert ok is False and "shorter than 12 months" in feedback and "pricing_matrix" in feedback

    valid["pricing_matrix"][0]["post_promo_price_usd_per_month"] = cited(60.0)  # fixed on the retry
    out = as_output(valid)
    ok, result = guardrail(out)
    assert ok is True and isinstance(out.pydantic, AnalystArtifact)  # downstream gets the STRICT model


def test_strategy_guardrail_turns_a_dangling_reference_into_feedback(valid, strategy):
    strategy["channels"][0]["target_icp_ids"] = ["ICP-9"]
    guardrail = make_strategy_guardrail(AnalystArtifact.model_validate(valid))
    ok, feedback = guardrail(as_output(strategy))
    assert ok is False and "ICPs that do not exist" in feedback

    strategy["channels"][0]["target_icp_ids"] = ["ICP-1"]
    out = as_output(strategy)
    assert guardrail(out)[0] is True and isinstance(out.pydantic, StrategyArtifact)


def plan_with_wire3() -> dict:
    """The shared PLAN fixture researches competitors only; the guardrail also requires Wire3 research."""
    plan = json.loads(PLAN.model_dump_json())
    plan["planned_tool_calls"].append({**plan["planned_tool_calls"][0],
                                       "args": {**plan["planned_tool_calls"][0]["args"], "company_name": "Wire3"}})
    return plan


def test_plan_guardrail_rejects_an_unbounded_plan_with_feedback():
    over = plan_with_wire3()
    over["budget"]["max_search_calls"] = 0  # one planned call now exceeds the budget
    ok, feedback = plan_guardrail(as_output(over))
    assert ok is False and "exceeds budget.max_search_calls" in feedback
    ok, out = plan_guardrail(as_output(plan_with_wire3()))
    assert ok is True and isinstance(out.pydantic, ResearchPlan)


def test_a_reply_that_is_not_json_is_feedback_too(valid):
    ok, feedback = make_analyst_guardrail(EVIDENCE)(SimpleNamespace(raw="Sorry, here is my analysis:", pydantic=None))
    assert ok is False and "not valid JSON" in feedback or "not a valid AnalystArtifact" in feedback


def test_plan_guardrail_requires_wire3_research_as_n8n_does():
    plan = plan_with_wire3()
    assert plan_guardrail(as_output(plan))[0] is True
    plan["planned_tool_calls"] = [c for c in plan["planned_tool_calls"] if c["args"]["company_name"].lower() != "wire3"]
    ok, feedback = plan_guardrail(as_output(plan))
    assert ok is False and "no Wire3 research calls" in feedback
