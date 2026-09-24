import re

import pytest
from test_analyst_models import EV, NAMES, valid  # noqa: F401
from test_run_store import EVIDENCE, PLAN
from test_strategy import strategy  # noqa: F401

from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.docs_content import DocsContentError, render_markdown, to_docs_requests
from wire3_gtm.executive_brief import DISCLAIMER, build_executive_brief
from wire3_gtm.strategy_models import StrategyArtifact

ID_RE = re.compile(r"\b(EV-[0-9a-f]{8}|THEME-\d+|SWOT-[SWOT]\d+|ASM-\d+|UNK-\d+|ICP-\d+|PILLAR-\d+|CHANNEL-\d+)\b")


def make(valid, strategy):
    return build_executive_brief(StrategyArtifact.model_validate(strategy),
                                 AnalystArtifact.model_validate(valid), EVIDENCE, PLAN, "run-test")


def test_sections(valid, strategy):
    assert make(valid, strategy).sections == [
        "The recommendation", "Why we can win", "What the market looks like", "Who we are targeting",
        "Competitive landscape", "Plan and timing", "Where the money goes", "How we will know it is working",
        "Top risks", "Open questions"]


def test_disclaimer_opens_and_closes_the_brief(valid, strategy):
    texts = [b.text for b in make(valid, strategy).blocks]
    assert DISCLAIMER in texts[:3] and texts[-1] == DISCLAIMER


def test_body_has_no_internal_ids(valid, strategy):
    assert not ID_RE.findall(render_markdown(make(valid, strategy)))


def test_judgment_calls_are_flagged(valid, strategy):
    strategy["value_proposition"]["headline"] = {"value": "Unsourced headline", "supporting_ids": [], "basis": "inference"}
    assert "Unsourced headline (judgment)" in render_markdown(make(valid, strategy))


def test_every_competitor_and_icp_appears(valid, strategy):
    md = render_markdown(make(valid, strategy)).replace("Wire3 (us)", "Wire3")
    assert all(name in md for name in NAMES) and "Promo-cliff switcher" in md


def test_both_gaps_are_answered(valid, strategy):
    md = render_markdown(make(valid, strategy))
    assert "No mobile bundle:" in md and "Low brand recognition:" in md


def test_sources_are_the_evidence_behind_included_content(valid, strategy):
    plan = make(valid, strategy)
    assert plan.expected["source_ids"] == [EV[0]]
    assert "[t](https://x.example)" in render_markdown(plan)


def test_refuses_invalid_content_like_the_full_plan(valid, strategy):
    strategy["icps"][0]["current_provider"] = {"value": ["Spectrum"], "supporting_ids": ["THEME-9"], "basis": "evidence"}
    with pytest.raises(DocsContentError, match="THEME-9"):
        make(valid, strategy)


def test_converts_to_google_docs_requests(valid, strategy):
    assert to_docs_requests(make(valid, strategy))[0]["insertText"]["location"] == {"index": 1}
