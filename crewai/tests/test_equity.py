"""The equity screen and the "Equity and compliance check" section (briefs with framing rules only)."""

import pytest
from test_analyst_models import valid  # noqa: F401
from test_brief_context import LAKE, lake_artifact
from test_run_store import EVIDENCE, PLAN
from test_strategy import c, strategy  # noqa: F401

from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.brief_context import using
from wire3_gtm.docs_content import build_document, render_markdown
from wire3_gtm.equity_checks import equity_flags
from wire3_gtm.strategy_checks import report
from wire3_gtm.strategy_models import StrategyArtifact


def flags_for(strategy, **rationale):
    for k, text in rationale.items():
        strategy["channels"][0]["rationale"] = c(text)
    with using(LAKE):
        return equity_flags(StrategyArtifact.model_validate(strategy))


def test_a_clean_strategy_has_no_flags(strategy):
    assert flags_for(strategy) == []


@pytest.mark.parametrize("text", [
    "Skip the high-crime blocks near downtown.",
    "Deprioritize low-income neighborhoods until phase 3.",
    "Charge higher deposits to renters with thin credit files.",
    "Avoid marketing to Spanish-speaking households for now.",
])
def test_problem_recommendations_are_flagged(strategy, text):
    (flag,) = flags_for(strategy, r=text)
    assert flag["where"] == "CHANNEL-1.rationale" and flag["text"] == text


@pytest.mark.parametrize("text", [
    "Do not exclude renters or manufactured-home residents from any offer.",
    "Offer the same price to every neighborhood, with no surcharge for apartments.",
    "Partner with libraries to reach seniors and low-income households.",
    "Avoid bill shock for renters with a price lock.",
])
def test_fair_recommendations_are_not_flagged(strategy, text):
    assert flags_for(strategy, r=text) == []


def test_pains_describe_the_market_and_are_not_screened(strategy):
    strategy["pains_and_outcomes"][0]["pain_statement"] = c("Renters pay higher prices after the promo ends.")
    assert flags_for(strategy) == []


def test_report_has_equity_flags_only_for_briefs_with_framing_rules(strategy):
    art = StrategyArtifact.model_validate(strategy)
    assert "equity_flags" not in report(art)  # Ocala: report unchanged
    with using(LAKE):
        assert report(art)["equity_flags"] == []


def lake_doc(valid, strategy):
    with using(LAKE):
        return render_markdown(build_document(StrategyArtifact.model_validate(strategy),
                                              AnalystArtifact.model_validate(lake_artifact(valid)), EVIDENCE, PLAN, "LAKE-t"))


def test_section_lists_rules_result_targeting_and_checklist(valid, strategy):
    md = lake_doc(valid, strategy)
    section = md.split("Equity and compliance check")[1].split("\n## ")[0]
    assert "stigmatizing descriptors" in section and "**Result:** nothing flagged." in section
    assert "**Promo-cliff switcher:** local groups" in section
    assert "not researched in this run" in section and "47 CFR Part 16" in section
    assert md.index("Equity and compliance check") < md.index("Risks")


def test_section_lists_flagged_statements(valid, strategy):
    strategy["channels"][0]["rationale"] = c("Deprioritize low-income neighborhoods until phase 3.")
    md = lake_doc(valid, strategy)
    assert "1 statement(s) flagged for review" in md and "Deprioritize low-income neighborhoods" in md


def test_ocala_document_has_no_equity_section(valid, strategy):
    doc = build_document(StrategyArtifact.model_validate(strategy), AnalystArtifact.model_validate(valid),
                         EVIDENCE, PLAN, "run-t")
    assert "Equity and compliance check" not in doc.sections


def test_run_summary_reports_flags_and_degrades(tmp_path):
    from wire3_gtm.run_log import Budget, RunMonitor
    from wire3_gtm.run_report import build_run_complete
    from wire3_gtm.run_store import RunStore

    store = RunStore("r", root=tmp_path)
    monitor = RunMonitor(store, Budget(), log_path=tmp_path / "l.jsonl")
    flags = [{"where": "CHANNEL-1.rationale", "text": "t", "reason": "r"}]
    out = build_run_complete(monitor, store, strategy_report={"basis_mix": {}, "equity_flags": flags})
    assert out["status"] == "degraded" and any("equity screen" in i for i in out["issues"])
