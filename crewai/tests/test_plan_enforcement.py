import json

from crewai.tools import BaseTool
from pydantic import BaseModel
from test_analyst_models import valid  # noqa: F401
from test_run_log import GOOD, ToolError
from test_run_store import make_steps
from test_strategy import strategy  # noqa: F401

from wire3_gtm.evidence import EvidenceCollector
from wire3_gtm.models import ResearchPlan
from wire3_gtm.pipeline import run_pipeline
from wire3_gtm.research_tools import RetryPolicy, _recording, enforce_plan
from wire3_gtm.run_log import Budget, RunMonitor
from wire3_gtm.run_store import RunStore


class Args(BaseModel):
    company_name: str
    region: str | None = None
    max_results: int = 10


def call(tool, company, region=None):
    return {"tool": tool, "args": {"company_name": company, "region": region}, "research_question_ids": ["RQ1"]}


def plan_of(*calls):
    return ResearchPlan.model_validate({
        "run_id": "p", "region": "Ocala", "segment": "s", "competitors": ["AT&T"],
        "research_questions": [{"id": "RQ1", "question": "q", "priority": "high"}],
        "planned_tool_calls": list(calls),
        "budget": {"max_search_calls": 40, "max_wall_clock_minutes": 12, "max_llm_calls_per_role": 6, "max_cost_usd": 2.5},
        "assumptions_and_gaps": []})


def fake_tool(name, script, log):
    """An MCP tool stand-in: follows `script` (an exception to raise or a string to return) and logs calls."""

    class Inner(BaseTool):
        name: str = "x"
        description: str = "d"
        args_schema: type = Args

        def _run(self, company_name: str, region: str | None = None, max_results: int = 10) -> str:
            log.append((name, company_name, region))
            step = script[min(len(log) - 1, len(script) - 1)] if not isinstance(script, str) else script
            if isinstance(step, Exception):
                raise step
            return step

    inner = Inner()
    inner.name = name
    return inner


def wrapped(names, tmp_path, script=GOOD, max_search=40, sink=None):
    store = RunStore("r1", root=tmp_path)
    monitor = RunMonitor(store, Budget(max_search_calls=max_search), log_path=tmp_path / "runs.jsonl")
    collector, log, sleeps = EvidenceCollector(sink=sink), [], []
    policy = RetryPolicy(sleep=sleeps.append)
    tools = [_recording(fake_tool(n, script, log), collector, monitor, policy) for n in names]
    return tools, collector, monitor, log, sleeps


def agent_makes(tools, collector, *made):
    """Simulate the agent making some of the planned calls."""
    by = {t.name: t for t in tools}
    for tool, company in made:
        by[tool].run(company_name=company)


# --- what counts as missing ---------------------------------------------------------------

def test_missing_planned_finds_exactly_the_calls_the_agent_did_not_make(tmp_path):
    plan = plan_of(call("recent_news", "AT&T"), call("recent_news", "Wire3"), call("product_portfolio_mapping", "Wire3"))
    tools, collector, *_ = wrapped(["recent_news", "product_portfolio_mapping"], tmp_path)
    agent_makes(tools, collector, ("recent_news", "AT&T"))
    assert [(p.tool, p.args.company_name) for p in collector.missing_planned(plan)] == [
        ("recent_news", "Wire3"), ("product_portfolio_mapping", "Wire3")]


def test_a_call_with_different_arguments_does_not_count_as_the_planned_one(tmp_path):
    plan = plan_of(call("recent_news", "AT&T"))
    tools, collector, *_ = wrapped(["recent_news"], tmp_path)
    agent_makes(tools, collector, ("recent_news", "Spectrum"))  # same tool, wrong company
    assert len(collector.missing_planned(plan)) == 1


def test_arguments_are_compared_ignoring_case_null_region_and_max_results(tmp_path):
    plan = plan_of(call("recent_news", "AT&T", None))
    tools, collector, *_ = wrapped(["recent_news"], tmp_path)
    tools[0].run(company_name=" at&t ", max_results=3)
    assert collector.missing_planned(plan) == []


def test_each_recorded_call_satisfies_only_one_planned_call(tmp_path):
    plan = plan_of(call("recent_news", "AT&T"), call("recent_news", "AT&T"))  # the same call planned twice
    tools, collector, *_ = wrapped(["recent_news"], tmp_path)
    agent_makes(tools, collector, ("recent_news", "AT&T"))
    assert len(collector.missing_planned(plan)) == 1


# --- running them -------------------------------------------------------------------------

def test_enforce_runs_only_the_missing_calls_and_marks_them(tmp_path):
    plan = plan_of(call("recent_news", "AT&T"), call("recent_news", "Wire3"), call("product_portfolio_mapping", "Wire3"))
    sink = tmp_path / "calls.jsonl"
    tools, collector, monitor, log, _ = wrapped(["recent_news", "product_portfolio_mapping"], tmp_path, sink=sink)
    agent_makes(tools, collector, ("recent_news", "AT&T"))
    log.clear()
    ran = enforce_plan(plan, collector, tools, monitor)
    assert sorted(ran) == ["product_portfolio_mapping(Wire3)", "recent_news(Wire3)"]
    assert sorted((t, c) for t, c, _ in log) == [("product_portfolio_mapping", "Wire3"), ("recent_news", "Wire3")]  # not AT&T again
    assert [c.source for c in collector.calls] == ["agent", "enforced", "enforced"]  # the record still shows who made each
    assert collector.missing_planned(plan) == [] and collector.coverage(plan)["enforced"] == 2
    assert [json.loads(line)["source"] for line in sink.read_text().splitlines()] == ["agent", "enforced", "enforced"]
    assert len(collector.build_evidence(plan)) >= 1  # the enforced results become evidence like any other


def test_enforce_is_idempotent_and_does_nothing_when_the_plan_is_complete(tmp_path):
    plan = plan_of(call("recent_news", "AT&T"))
    tools, collector, monitor, log, _ = wrapped(["recent_news"], tmp_path)
    agent_makes(tools, collector, ("recent_news", "AT&T"))
    log.clear()
    assert enforce_plan(plan, collector, tools, monitor) == [] and log == []
    plan2 = plan_of(call("recent_news", "AT&T"), call("recent_news", "Wire3"))
    assert len(enforce_plan(plan2, collector, tools, monitor)) == 1
    log.clear()
    assert enforce_plan(plan2, collector, tools, monitor) == [] and log == []  # a second pass runs nothing


def test_enforced_calls_get_the_same_retries_and_backoff(tmp_path):
    plan = plan_of(call("recent_news", "Wire3"))
    tools, collector, monitor, log, sleeps = wrapped(
        ["recent_news"], tmp_path, script=[ToolError("search provider failed: 503"), GOOD])
    enforce_plan(plan, collector, tools, monitor)
    (rec,) = collector.calls
    assert rec.source == "enforced" and rec.status == "ok" and rec.attempts == 2 and sleeps == [2.0]


def test_enforced_calls_respect_the_search_budget(tmp_path):
    plan = plan_of(call("recent_news", "A"), call("recent_news", "B"), call("recent_news", "C"))
    tools, collector, monitor, log, _ = wrapped(["recent_news"], tmp_path, max_search=1)
    enforce_plan(plan, collector, tools, monitor)  # must not raise
    assert sorted(c.status for c in collector.calls) == ["budget_exceeded", "budget_exceeded", "ok"]
    assert monitor.search_calls == 1 and len(log) == 1  # only one real search was made


def test_a_planned_tool_that_does_not_exist_is_recorded_as_a_failure_not_a_crash(tmp_path):
    plan = plan_of(call("company_overview", "Wire3"))
    tools, collector, monitor, *_ = wrapped(["recent_news"], tmp_path)  # company_overview is not available
    enforce_plan(plan, collector, tools, monitor)
    (rec,) = collector.calls
    assert rec.status == "failed" and "no research tool named" in rec.error and rec.source == "enforced"


# --- in the run record --------------------------------------------------------------------

def test_a_recovered_skip_is_reported_but_does_not_degrade_the_run(tmp_path, valid, strategy):
    store = RunStore("r1", root=tmp_path)
    monitor = RunMonitor(store, log_path=tmp_path / "runs.jsonl")
    steps = make_steps(valid, strategy)
    inner = steps["research"].fn

    def research_with_a_recovered_skip(*a):
        phase = inner(*a)
        phase.collector.calls[-1].source = "enforced"  # the pipeline made the call the agent skipped
        return phase

    steps["research"].fn = research_with_a_recovered_skip
    run_pipeline({"b": 1}, store=store, steps=steps, monitor=monitor)
    rc = json.loads(store.load_text("06_run_complete.json"))
    assert rc["status"] == "success"  # the plan was completed
    assert any("skipped 1 planned call(s); the pipeline ran them" in i for i in rc["issues"])
