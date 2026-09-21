import json
from types import SimpleNamespace

import pytest
from test_analyst_models import EV, valid  # noqa: F401
from test_strategy import strategy  # noqa: F401

from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.evidence import EvidenceCollector, EvidenceRecord
from wire3_gtm.models import ResearchPlan
from wire3_gtm.pipeline import _recording_guardrail, run_pipeline
from wire3_gtm.run_store import RunStore
from wire3_gtm.strategy_models import StrategyArtifact

PLAN = ResearchPlan.model_validate({
    "run_id": "plan-1", "region": "Ocala", "segment": "s", "competitors": ["AT&T"],
    "research_questions": [{"id": "RQ1", "question": "q", "priority": "high"}],
    "planned_tool_calls": [{"tool": "recent_news", "args": {"company_name": "AT&T"}, "research_question_ids": ["RQ1"]}],
    "budget": {"max_search_calls": 40, "max_wall_clock_minutes": 12, "max_llm_calls_per_role": 6, "max_cost_usd": 2.5},
    "assumptions_and_gaps": [],
})
EVIDENCE = [EvidenceRecord(evidence_id=EV[0], research_question_id="RQ1", claim="c", source_title="t",
                           source_url="https://x.example", source_type="company",
                           retrieval_timestamp="2026-09-20T00:00:00Z", excerpt="e")]


class Counter:
    def __init__(self, fn):
        self.fn, self.calls = fn, 0

    def __call__(self, *a):
        self.calls += 1
        return self.fn(*a)


def make_steps(valid, strategy, strategy_fails=False):
    def research(brief, store, monitor=None):
        return SimpleNamespace(plan=PLAN, evidence=EVIDENCE, collector=EvidenceCollector())

    def analyst(plan, evidence, store, monitor=None):
        return SimpleNamespace(artifact=AnalystArtifact.model_validate(valid), unsupported_numbers=[],
                               theme_rq_repairs=[], source_quality={}, dropped_ids=[])

    def strat(analyst_artifact, store, monitor=None):
        if strategy_fails:
            raise RuntimeError("guardrail exhausted")
        return SimpleNamespace(artifact=StrategyArtifact.model_validate(strategy), report={})

    links = lambda store, analyst_artifact, evidence: {  # noqa: E731
        "urls_total": 1, "invalid_url_count": 0, "broken_url_count": 0, "blocked": 0, "ok": 1, "unchecked": 0}
    return {"research": Counter(research), "analyst": Counter(analyst), "strategy": Counter(strat), "links": links}


def test_failure_in_last_step_keeps_earlier_artifacts(tmp_path, valid, strategy):
    store = RunStore("r1", root=tmp_path)
    with pytest.raises(RuntimeError, match="guardrail exhausted"):
        run_pipeline({"b": 1}, store=store, steps=make_steps(valid, strategy, strategy_fails=True))
    for name in ("00_brief.json", "01_plan.json", "02_evidence_set.json", "02_research_report.json",
                 "03_analyst_artifact.json", "03_analyst_report.json"):
        assert store.exists(name), name
    assert not store.exists("04_strategy_artifact.json")
    m = store.manifest()
    assert m["steps"]["research"]["status"] == "ok" and m["steps"]["analyst"]["status"] == "ok"
    assert m["steps"]["strategy"]["status"] == "failed" and "guardrail exhausted" in m["steps"]["strategy"]["error"]
    assert m["status"] == "failed"


def test_resume_does_not_repeat_finished_steps(tmp_path, valid, strategy):
    store = RunStore("r1", root=tmp_path)
    with pytest.raises(RuntimeError):
        run_pipeline({"b": 1}, store=store, steps=make_steps(valid, strategy, strategy_fails=True))
    steps = make_steps(valid, strategy)  # fresh counters, strategy now works
    result = run_pipeline({"b": 1}, store=RunStore("r1", root=tmp_path), steps=steps)
    assert steps["research"].calls == 0 and steps["analyst"].calls == 0 and steps["strategy"].calls == 1
    assert result.strategy.brief_id == "run-1"
    m = RunStore("r1", root=tmp_path).manifest()
    assert m["steps"]["research"]["status"] == "resumed" and m["steps"]["strategy"]["status"] == "ok"
    assert m["status"] == "complete"


def test_rejected_drafts_are_kept_when_retries_run_out(tmp_path):
    store = RunStore("r1", root=tmp_path)
    guard = _recording_guardrail(store, "03_analyst", lambda out: (False, "theme RQs wrong"))
    for i in range(3):
        guard(SimpleNamespace(raw=f'{{"draft": {i}}}'))
    saved = sorted(p.name for p in (store.dir / "attempts").iterdir())
    assert saved == [f"03_analyst_attempt_{i}.json" for i in (1, 2, 3)]
    import json
    third = json.loads(store.load_text("attempts/03_analyst_attempt_3.json"))
    assert third["accepted"] is False and third["feedback"] == "theme RQs wrong" and '"draft": 2' in third["raw"]


def test_writes_are_atomic_and_leave_no_temp_files(tmp_path):
    store = RunStore("r1", root=tmp_path)
    store.save_json("x.json", {"a": 1})
    store.save_json("x.json", {"a": 2})
    assert store.load_text("x.json").count('"a": 2') == 1
    assert not list(store.dir.glob("*.tmp"))


def test_tool_calls_hit_disk_as_they_return_and_can_be_salvaged(tmp_path):
    sink = tmp_path / "calls.jsonl"
    c = EvidenceCollector(sink=sink)
    c.record("recent_news", {"company_name": "AT&T"},
             '{"source_title": "t", "source_url": "https://x.example", "excerpt": "e", "retrieval_timestamp": "T"}')
    c.record("recent_news", {"company_name": "Spectrum"}, None, error="ConnectError: down")
    assert len(sink.read_text().splitlines()) == 2  # on disk before any evidence set exists
    rebuilt = EvidenceCollector.from_jsonl(sink)
    assert [x.status for x in rebuilt.calls] == ["ok", "failed"]
    assert len(rebuilt.build_evidence(PLAN)) == 1


# --- salvage a failed Analyst step from its saved drafts ----------------------

def _seed_failed_analyst_run(tmp_path, valid, drafts):
    from wire3_gtm.analyst_models import dump_contract
    from wire3_gtm.evidence import EvidenceSet

    store = RunStore("r1", root=tmp_path)
    store.save_json("02_evidence_set.json", EvidenceSet(run_id="plan-1", evidence=EVIDENCE).model_dump(mode="json"))
    for n, raw in enumerate(drafts, 1):
        store.save_attempt("03_analyst", n, raw, "rejected")
    return store, dump_contract


def test_salvage_recovers_a_draft_that_only_failed_on_an_invented_id(tmp_path, valid):
    import json

    from wire3_gtm.analyst_models import dump_contract
    from wire3_gtm.pipeline import salvage_analyst

    bad = json.loads(json.dumps(dump_contract(AnalystArtifact.model_validate(valid))))
    bad["competitor_comparison_table"][1]["footprint_confirmed_in_region"]["evidence_ids"] = ["EV-aaaaaaab"]
    store, _ = _seed_failed_analyst_run(tmp_path, valid, ["not json at all", json.dumps(bad)])
    with pytest.raises(RuntimeError):  # step failed for real, as in the live run
        with store.step("analyst"):
            raise RuntimeError("guardrail exhausted")
    salvage_analyst(store)
    report = json.loads(store.load_text("03_analyst_report.json"))
    assert report["salvaged_from"] == "03_analyst_attempt_2.json"  # newest usable draft
    assert report["dropped_ids"][0]["dropped"] == ["EV-aaaaaaab"]
    assert store.manifest()["steps"]["analyst"]["status"] == "salvaged"
    AnalystArtifact.model_validate_json(store.load_text("03_analyst_artifact.json"))


def test_salvage_refuses_when_no_draft_passes(tmp_path, valid):
    from wire3_gtm.pipeline import salvage_analyst

    store, _ = _seed_failed_analyst_run(tmp_path, valid, ["not json", "{}"])
    with pytest.raises(RuntimeError, match="no saved Analyst draft passes"):
        salvage_analyst(store)
    assert not store.exists("03_analyst_artifact.json")


def test_resuming_a_salvaged_step_keeps_the_salvaged_status(tmp_path):
    store = RunStore("r1", root=tmp_path)
    store.mark_salvaged("analyst", salvaged_from="03_analyst_attempt_4.json")
    store.mark_resumed("analyst")
    step = store.manifest()["steps"]["analyst"]
    assert step["status"] == "salvaged" and step["salvaged_from"] == "03_analyst_attempt_4.json"
    store.mark_resumed("research")
    with store.step("strategy"):
        pass
    assert store.manifest()["status"] == "complete"  # salvaged counts as a finished step


def test_a_research_crash_can_be_salvaged_from_the_searches_already_saved(tmp_path):
    """Research died part-way: the plan and the tool calls that returned are on disk, and no
    search has to be paid for again."""
    from wire3_gtm.pipeline import salvage_research

    store = RunStore("r1", root=tmp_path)
    store.save_text("01_plan.json", PLAN.model_dump_json())
    c = EvidenceCollector(sink=store.path("02_tool_calls.jsonl"))
    c.record("recent_news", {"company_name": "AT&T"},
             '{"source_title": "t", "source_url": "https://x.example", "excerpt": "e", "retrieval_timestamp": "2026-09-20T00:00:00Z"}',
             attempts=2)
    with pytest.raises(RuntimeError):
        with store.step("research"):
            raise RuntimeError("research crashed at call 2")
    salvage_research(store)
    from wire3_gtm.evidence import EvidenceSet
    assert len(EvidenceSet.model_validate_json(store.load_text("02_evidence_set.json")).evidence) == 1
    step = store.manifest()["steps"]["research"]
    assert step["status"] == "salvaged" and step["tool_calls_recovered"] == 1
    assert json.loads(store.load_text("02_research_report.json"))["salvaged_from"] == "02_tool_calls.jsonl"


def test_salvage_research_refuses_when_there_is_nothing_to_recover(tmp_path):
    from wire3_gtm.pipeline import salvage_research

    store = RunStore("r1", root=tmp_path)
    with pytest.raises(RuntimeError, match="nothing to salvage"):
        salvage_research(store)
    store.save_text("01_plan.json", PLAN.model_dump_json())
    store.save_text("02_tool_calls.jsonl", "")
    with pytest.raises(RuntimeError, match="no evidence"):
        salvage_research(store)
