"""The pipeline fetches the brief's Census geographies in code and turns them into RQ-tagged evidence."""

import json
from contextlib import contextmanager

import pytest
from crewai.tools import BaseTool
from pydantic import BaseModel
from test_analyst_models import valid  # noqa: F401
from test_plan_enforcement import call, plan_of
from test_strategy import strategy  # noqa: F401

from wire3_gtm.brief_context import OCALA, from_brief, load
from wire3_gtm.evidence import EvidenceCollector
from wire3_gtm.research_tools import CENSUS_TOOL, McpUnavailable, RetryPolicy, run_census
from wire3_gtm.run_log import Budget, RunMonitor
from wire3_gtm.run_store import RunStore

LAKE = from_brief(load("lake-county"))


def census_result(geography):
    return json.dumps([{"source_title": f"{geography}: housing (ACS)", "source_url": "https://data.census.gov/profile?g=X",
                        "excerpt": f"{geography} (ACS 2020-2024): 37.4% renter-occupied.", "publication_date": None,
                        "retrieval_timestamp": "2026-09-24T00:00:00Z"}])


class GeoArgs(BaseModel):
    geography: str


def fake_census(log, fail=()):
    class Census(BaseTool):
        name: str = CENSUS_TOOL
        description: str = "d"
        args_schema: type = GeoArgs

        def _run(self, geography: str) -> str:
            log.append(geography)
            if geography in fail:
                raise RuntimeError("census API returned HTTP 400: unknown geography")
            return census_result(geography)

    return Census()


def adapter_with(*tools):
    @contextmanager
    def adapter(params):
        yield list(tools)
    return adapter


def setup(tmp_path, max_search=40):
    store = RunStore("r1", root=tmp_path)
    monitor = RunMonitor(store, Budget(max_search_calls=max_search), log_path=tmp_path / "runs.jsonl")
    return EvidenceCollector(), monitor, RetryPolicy(sleep=lambda s: None)


def test_lake_brief_lists_census_geographies_and_ocala_has_none():
    assert LAKE.census_rq == "RQ5" and "ZIP 34788" in LAKE.census_geographies and len(LAKE.census_geographies) == 6
    assert OCALA.census_geographies == ()


def test_census_evidence_is_tagged_with_the_briefs_research_question(tmp_path):
    collector, monitor, policy = setup(tmp_path)
    log = []
    run_census(("Leesburg city, FL", "ZIP 34788"), "RQ5", collector, monitor=monitor, policy=policy,
               adapter=adapter_with(fake_census(log)))
    assert log == ["Leesburg city, FL", "ZIP 34788"]
    assert [c.source for c in collector.calls] == ["census", "census"]
    evidence = collector.build_evidence(plan_of(call("recent_news", "Wire3")))
    assert {e.research_question_id for e in evidence} == {"RQ5"} and len(evidence) == 2
    assert {e.source_type for e in evidence} == {"primary"}  # data.census.gov is a .gov source
    assert collector.research_question_ids == [] and collector.source == "agent"  # reset for later calls


def test_census_calls_count_against_the_search_budget(tmp_path):
    collector, monitor, policy = setup(tmp_path, max_search=1)
    run_census(("ZIP 34748", "ZIP 34788"), "RQ5", collector, monitor=monitor, policy=policy,
               adapter=adapter_with(fake_census([])))
    assert [c.status for c in collector.calls] == ["ok", "budget_exceeded"]


def test_a_failed_geography_is_recorded_and_the_rest_still_run(tmp_path):
    collector, monitor, policy = setup(tmp_path)
    log = []
    run_census(("ZIP 00000", "ZIP 34788"), "RQ5", collector, monitor=monitor, policy=policy,
               adapter=adapter_with(fake_census(log, fail={"ZIP 00000"})))
    assert log == ["ZIP 00000", "ZIP 34788"] and [c.status for c in collector.calls] == ["failed", "ok"]


def test_no_geographies_means_no_connection(tmp_path):
    def never(params):
        raise AssertionError("must not connect")
    assert run_census((), None, EvidenceCollector(), adapter=never) == []


def test_a_server_without_the_census_tool_is_a_clear_error(tmp_path):
    collector, monitor, policy = setup(tmp_path)
    with pytest.raises(McpUnavailable, match="census_profile"):
        run_census(("ZIP 34748",), "RQ5", collector, monitor=monitor, policy=policy, adapter=adapter_with())


def test_census_calls_do_not_hide_a_planned_call_the_agent_skipped(tmp_path):
    collector, monitor, policy = setup(tmp_path)
    run_census(("ZIP 34748",), "RQ5", collector, monitor=monitor, policy=policy, adapter=adapter_with(fake_census([])))
    plan = plan_of(call("recent_news", "Wire3"))
    assert len(collector.missing_planned(plan)) == 1
    cov = collector.coverage(plan)
    assert cov["census"] == 1 and cov["executed"] - cov["unplanned"] == 0


# --- the "Market profile by city" section ------------------------------------------------------

def census_evidence():
    from wire3_gtm.evidence import EvidenceRecord, make_evidence_id

    label = "U.S. Census Bureau, American Community Survey 2020-2024 5-year estimates"
    rows = [("Leesburg city, Florida", "160XX00US1239875", "housing", "12,833 households; 37.4% renter-occupied"),
            ("Leesburg city, Florida", "160XX00US1239875", "income and poverty", "median household income $52,880"),
            ("ZCTA5 34788", "860XX00US34788", "housing", "9,522 households; 51.1% mobile homes")]
    out = []
    for name, geo, topic, figures in rows:
        url, claim = f"https://data.census.gov/profile?g={geo}", f"{name} ({label}): {figures}."
        out.append(EvidenceRecord(evidence_id=make_evidence_id(url, claim), research_question_id="RQ5", claim=claim,
                                  source_title=f"{name}: {topic} ({label})", source_url=url, source_type="primary",
                                  retrieval_timestamp="2026-09-24T00:00:00Z", excerpt=claim))
    return out


def test_market_profile_section_quotes_census_figures_exactly_with_citations(valid, strategy):  # noqa: F811
    from test_run_store import EVIDENCE, PLAN

    from wire3_gtm.analyst_models import AnalystArtifact
    from wire3_gtm.docs_content import build_document, render_markdown
    from wire3_gtm.strategy_models import StrategyArtifact

    census = census_evidence()
    doc = build_document(StrategyArtifact.model_validate(strategy), AnalystArtifact.model_validate(valid),
                         EVIDENCE + census, PLAN, "LAKE-test")
    assert doc.sections[:3] == ["Executive summary", "Research scope and evidence", "Market profile by city"]
    md = render_markdown(doc)
    assert "### Leesburg city, Florida" in md and "### ZIP area 34788" in md
    assert f"**Housing:** 12,833 households; 37.4% renter-occupied [{census[0].evidence_id}]" in md
    assert "51.1% mobile homes" in md and all(e.evidence_id in doc.expected["source_ids"] for e in census)


def test_no_census_evidence_means_no_market_profile_section(valid, strategy):  # noqa: F811
    from test_run_store import EVIDENCE, PLAN

    from wire3_gtm.analyst_models import AnalystArtifact
    from wire3_gtm.docs_content import build_document
    from wire3_gtm.strategy_models import StrategyArtifact

    doc = build_document(StrategyArtifact.model_validate(strategy), AnalystArtifact.model_validate(valid),
                         EVIDENCE, PLAN, "run-test")
    assert "Market profile by city" not in doc.sections


def test_executive_brief_summarizes_the_local_market_from_census(valid, strategy):  # noqa: F811
    from test_run_store import EVIDENCE, PLAN

    from wire3_gtm.analyst_models import AnalystArtifact
    from wire3_gtm.docs_content import render_markdown
    from wire3_gtm.executive_brief import build_executive_brief
    from wire3_gtm.strategy_models import StrategyArtifact

    md = render_markdown(build_executive_brief(StrategyArtifact.model_validate(strategy),
                                               AnalystArtifact.model_validate(valid), EVIDENCE + census_evidence(), PLAN, "LAKE-test"))
    assert "Local market (U.S. Census" in md
    assert "**Leesburg city, Florida:** 12,833 households; 37.4% renter-occupied; median household income $52,880." in md
    assert "EV-" not in md.split("## Sources behind this brief")[0]  # figures, not ids, in the body
