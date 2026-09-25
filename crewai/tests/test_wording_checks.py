import json
from types import SimpleNamespace

from test_analyst_models import EV, valid  # noqa: F401
from test_analyst_speed import rec
from test_strategy import strategy  # noqa: F401

from wire3_gtm.analyst_checks import make_analyst_guardrail
from wire3_gtm.analyst_models import BLENDED_FORMULA, AnalystArtifact
from wire3_gtm.strategy_checks import make_strategy_guardrail, report
from wire3_gtm.strategy_models import StrategyArtifact
from wire3_gtm.wording_checks import internal_terms

# Shipped in run LAKE-20260924-233944 (assumption ASM-2's rationale)
LIVE = ("Wire 3 company pages do not mention mobile bundles in the EvidenceSet; developer brief requires "
        "Wire3 mobile-bundle field to be brief_stated false.")
EVIDENCE = [rec(e, "RQ2") for e in EV]


def reply(d):
    return SimpleNamespace(raw=json.dumps(d), pydantic=None)


def test_the_live_sentence_is_caught_term_by_term():
    found = internal_terms({"assumptions": [{"rationale": LIVE}]})
    assert [f["term"] for f in found] == ["EvidenceSet", "developer brief", "brief_stated"]
    assert found[0]["path"] == "assumptions[0].rationale" and "the research" in found[0]["fix"]


def test_ordinary_prose_ids_urls_and_the_code_written_formula_are_not_flagged():
    doc = {
        "value": "AutoPay and WiFi gateways; local field technicians fill the sales pipeline.",
        "basis": "brief_stated",  # an enum value, not prose
        "evidence_ids": ["EV-aaaaaaaa"],
        "note": "Plans at https://web.archive.org/web/2026/https://x.example/plan_page?a_b=1 and www.x.example/a_b.",
        "formula": BLENDED_FORMULA,
        "end": "The price is not in the evidence set.",
    }
    assert [f["term"] for f in internal_terms(doc)] == ["evidence set"]
    assert [f["term"] for f in internal_terms({"t": "field is brief_stated."})] == ["brief_stated"]  # ends a sentence


def test_the_analyst_draft_is_sent_back_once_then_accepted(valid):
    valid["market_themes"][0]["description"] = LIVE
    guard = make_analyst_guardrail(EVIDENCE, degrade=lambda: False, gaps_log=[])
    ok, feedback = guard(reply(valid))
    assert ok is False and "'EvidenceSet' in market_themes[0].description" in feedback
    assert feedback.split("Problems: ")[1].startswith("Text fields are read")  # first, so never cut off
    ok, out = guard(reply(valid))  # the same wording again: accepted, left for the report
    assert ok is True and isinstance(out.pydantic, AnalystArtifact)


def test_no_time_left_accepts_the_wording_at_once(valid):
    valid["market_themes"][0]["description"] = LIVE
    ok, _ = make_analyst_guardrail(EVIDENCE, degrade=lambda: True, gaps_log=[])(reply(valid))
    assert ok is True


def test_clean_analyst_wording_is_not_sent_back(valid):
    ok, _ = make_analyst_guardrail(EVIDENCE, degrade=lambda: False, gaps_log=[])(reply(valid))
    assert ok is True


def test_the_strategy_draft_is_sent_back_once_then_accepted_and_reported(valid, strategy):
    strategy["channels"][0]["rationale"]["value"] = "Cheap; the Analyst artifact shows no evidence set gaps."
    guard = make_strategy_guardrail(AnalystArtifact.model_validate(valid))
    ok, feedback = guard(reply(strategy))
    assert ok is False and "'Analyst artifact' in channels[0].rationale.value" in feedback
    ok, out = guard(reply(strategy))
    assert ok is True
    assert [f["term"] for f in report(out.pydantic)["internal_wording"]] == ["Analyst artifact", "evidence set"]
    assert report(StrategyArtifact.model_validate(json.loads(json.dumps(strategy)) | {
        "channels": [strategy["channels"][0] | {"rationale": {"value": "cheap", "supporting_ids": [],
                                                               "basis": "inference"}}]}))["internal_wording"] == []
