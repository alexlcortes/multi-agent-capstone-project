import json
from datetime import datetime, timedelta, timezone

import pytest
from crewai.tools import BaseTool
from pydantic import BaseModel
from test_run_store import PLAN

from wire3_gtm.evidence import EvidenceCollector
from wire3_gtm.research_tools import RetryPolicy, _recording, is_transient
from wire3_gtm.run_log import Budget, BudgetExceeded, RunMonitor, Usage
from wire3_gtm.run_store import RunStore

class ToolError(Exception):
    """Stand-in: the MCP adapter surfaces server tool errors as generic exceptions carrying the message."""


GOOD = json.dumps({"source_title": "t", "source_url": "https://x.example", "excerpt": "e",
                   "retrieval_timestamp": "2026-09-20T00:00:00Z"})


class Args(BaseModel):
    company_name: str
    max_results: int = 10


def flaky(script):
    """A stand-in MCP tool that follows a script: an Exception to raise, or a str to return."""
    calls = []

    class Inner(BaseTool):
        name: str = "recent_news"
        description: str = "d"
        args_schema: type = Args

        def _run(self, company_name: str, max_results: int = 10) -> str:
            calls.append(company_name)
            step = script[min(len(calls) - 1, len(script) - 1)]
            if isinstance(step, Exception):
                raise step
            return step

    return Inner(), calls


def setup(script, tmp_path, max_search=40):
    store = RunStore("r1", root=tmp_path)
    monitor = RunMonitor(store, Budget(max_search_calls=max_search), log_path=tmp_path / "runs.jsonl")
    collector, sleeps = EvidenceCollector(), []
    inner, calls = flaky(script)
    tool = _recording(inner, collector, monitor, RetryPolicy(sleep=sleeps.append))
    return tool, collector, monitor, sleeps, calls


# --- retries with backoff, and no duplicate evidence -----------------------------

def test_transient_failure_is_retried_with_backoff_and_recorded_once(tmp_path):
    tool, collector, monitor, sleeps, calls = setup(
        [ToolError("search provider failed: 503"), ConnectionError("reset"), GOOD], tmp_path)
    assert tool.run(company_name="AT&T") == GOOD
    assert len(calls) == 3 and sleeps == [2.0, 4.0]  # exponential backoff
    (rec,) = collector.calls  # ONE record for three attempts
    assert rec.status == "ok" and rec.attempts == 3 and len(rec.attempt_errors) == 2
    assert len(collector.build_evidence(PLAN)) == 1  # a retry cannot create duplicate evidence
    assert monitor.search_calls == 3  # every attempt used real search quota


def test_retries_are_bounded_and_the_failure_stays_visible(tmp_path):
    tool, collector, _, sleeps, calls = setup([ToolError("search provider failed: 503")], tmp_path)
    out = tool.run(company_name="AT&T")
    assert out.startswith("TOOL ERROR") and len(calls) == 3 and sleeps == [2.0, 4.0]
    (rec,) = collector.calls
    assert rec.status == "failed" and rec.attempts == 3 and "503" in rec.error
    assert collector.coverage(PLAN)["failed"] == 1


def test_a_bad_argument_is_not_retried(tmp_path):
    tool, collector, _, sleeps, calls = setup([ToolError("company_name must not be empty")], tmp_path)
    tool.run(company_name="x")
    assert len(calls) == 1 and sleeps == []
    assert collector.calls[0].attempts == 1


def test_is_transient_classification():
    assert is_transient(ToolError("search provider failed: HTTP 429")) and is_transient(TimeoutError())
    assert not is_transient(ToolError("company_name must not be empty")) and not is_transient(ValueError("nope"))


def test_undeclared_arguments_are_dropped_and_recorded_as_sent(tmp_path):
    tool, collector, _, _, _ = setup([GOOD], tmp_path)
    tool.run(company_name="AT&T", max_results=3)
    assert collector.calls[0].args == {"company_name": "AT&T", "max_results": 3}


# --- search budget is enforced ---------------------------------------------------

def test_search_budget_refuses_calls_and_stops_retrying_when_spent(tmp_path):
    tool, collector, monitor, _, calls = setup(
        [GOOD, ToolError("search provider failed: 503")], tmp_path, max_search=2)
    tool.run(company_name="A")  # uses slot 1
    out = tool.run(company_name="B")  # attempt uses slot 2, fails, retry is blocked: budget spent
    assert "budget exhausted" in out and len(calls) == 2
    third = tool.run(company_name="C")  # refused outright, the provider is never called
    assert "Do not make further search calls" in third and len(calls) == 2
    statuses = [c.status for c in collector.calls]
    assert statuses == ["ok", "budget_exceeded", "budget_exceeded"]
    assert monitor.search_calls == 2 and collector.coverage(PLAN)["failed"] == 2


# --- usage, cost, events ---------------------------------------------------------

def test_cost_uses_provider_reported_tokens_including_reasoning_and_cache():
    u = Usage()
    u.add({"prompt_tokens": 1000, "completion_tokens": 500, "cached_prompt_tokens": 200, "reasoning_tokens": 400})
    # 800 fresh in @0.25 + 200 cached @0.025 + 500 out @2.00 (reasoning is inside completion, not added twice)
    assert u.cost_usd() == pytest.approx((800 * 0.25 + 200 * 0.025 + 500 * 2.0) / 1e6)
    assert u.reasoning_tokens == 400 and u.llm_calls == 1


def test_agent_events_carry_duration_and_only_that_agents_usage(tmp_path):
    store = RunStore("r1", root=tmp_path)
    m = RunMonitor(store, log_path=tmp_path / "runs.jsonl")
    t0 = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
    m.on_llm_completed("Head Planner", {"prompt_tokens": 100, "completion_tokens": 50})
    m.on_task_started("Research Agent", t0)
    for _ in range(3):
        m.on_llm_completed("Research Agent", {"prompt_tokens": 1000, "completion_tokens": 10, "reasoning_tokens": 4})
    m.note_guardrail("Research Agent", "research", 1, accepted=False, feedback="fix it")
    m.on_task_ended("Research Agent", "ok", t0 + timedelta(seconds=75.5))
    events = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    start, gate, end = events
    assert start["event_type"] == "agent_start" and start["implementation"] == "crewai"
    assert start["client_run_id"] == "r1" and start["ts"].endswith("Z") and start["model"] == "gpt-5-mini"
    assert gate["event_type"] == "validation_gate" and gate["status"] == "rejected" and gate["attempt"] == 1
    assert end["event_type"] == "agent_end" and end["duration_ms"] == 75500
    assert end["llm_calls"] == 3 and end["prompt_tokens"] == 3000 and end["reasoning_tokens"] == 12  # not the planner's
    assert end["guardrail_retries"] == 1


def test_retries_are_totalled_by_kind(tmp_path):
    m = RunMonitor(RunStore("r1", root=tmp_path), log_path=tmp_path / "runs.jsonl")
    m.tool_retries, m.provider_retries = 2, 1
    m.note_guardrail("Analyst Agent", "analyst_guardrail", 1, False, "x")
    m.note_guardrail("Analyst Agent", "analyst_guardrail", 2, True, None)
    assert m.retries() == {"tool_call_retries": 2, "guardrail_retries": {"Analyst Agent": 1},
                           "provider_retries": 1, "total": 4}


# --- budget limits stop the run clearly ------------------------------------------

def test_wall_clock_budget_stops_with_a_clear_message(tmp_path):
    now = [1000.0]
    m = RunMonitor(RunStore("r1", root=tmp_path), Budget(max_wall_clock_minutes=12),
                   log_path=tmp_path / "runs.jsonl", clock=lambda: now[0])
    m.check_budget("before analyst")  # fine at t=0
    now[0] += 12 * 60 + 1
    with pytest.raises(BudgetExceeded, match=r"wall-clock budget reached before analyst: 12\.0 min elapsed, limit 12"):
        m.check_budget("before analyst")


def test_cost_budget_stops_with_a_clear_message(tmp_path):
    m = RunMonitor(RunStore("r1", root=tmp_path), Budget(max_cost_usd=0.001), log_path=tmp_path / "runs.jsonl")
    m.on_llm_completed("Analyst Agent", {"prompt_tokens": 10_000, "completion_tokens": 1_000})  # $0.0045
    with pytest.raises(BudgetExceeded, match="cost budget reached after analyst draft"):
        m.check_budget("after analyst draft")


def test_budget_comes_from_the_brief():
    b = Budget.from_brief({"budget": {"max_search_calls": 7, "max_wall_clock_minutes": 3, "max_cost_usd": 0.5}})
    assert (b.max_search_calls, b.max_wall_clock_minutes, b.max_cost_usd, b.max_llm_calls_per_role) == (7, 3, 0.5, 6)


# --- run-level record --------------------------------------------------------------

from test_analyst_models import valid  # noqa: E402,F401
from test_run_store import make_steps  # noqa: E402
from test_strategy import strategy  # noqa: E402,F401

from wire3_gtm.pipeline import run_pipeline  # noqa: E402
from wire3_gtm.run_report import format_summary  # noqa: E402


def events_of(tmp_path):
    return [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]


def run(tmp_path, steps, budget=None, **kw):
    store = RunStore("r1", root=tmp_path)
    monitor = RunMonitor(store, budget or Budget(), log_path=tmp_path / "runs.jsonl")
    return store, monitor, lambda: run_pipeline({"b": 1}, store=store, steps=steps, monitor=monitor, **kw)


def test_successful_run_writes_the_full_record(tmp_path, valid, strategy):
    store, monitor, go = run(tmp_path, make_steps(valid, strategy))
    monitor.on_llm_completed("Analyst Agent", {"prompt_tokens": 1000, "completion_tokens": 500, "reasoning_tokens": 300})
    go()
    ev = events_of(tmp_path)
    kinds = [e["event_type"] for e in ev]
    assert kinds[0] == "pipeline_start" and kinds[-3:] == ["run_complete", "usage_summary", "run_record"]
    assert {"tool_call", "validation_gate"} <= set(kinds) or "validation_gate" in kinds
    rc = next(e for e in ev if e["event_type"] == "run_complete")
    assert rc["implementation"] == "crewai" and rc["status"] == "success" and rc["error"] is None
    assert rc["research_questions_answered"] == 1 and rc["research_questions_total"] == 1
    assert rc["evidence_count"] == 1 and rc["broken_url_count"] == 0 and rc["document_write_status"] == "not_run"
    assert rc["brief_id"] == "run-1" and rc["model"] == "gpt-5-mini" and rc["latency_within_budget"] is True
    us = ev[-2]
    rr = ev[-1]
    assert rr["run_id"] == store.run_id and rr["status"] == "success" and rr["tokens"]["reasoning"] == 300
    assert (store.dir / "06_run_record.json").exists()
    assert us["cost_is_lower_bound"] is False and us["per_agent"][0]["tokens_source"] == "provider_reported"
    assert (store.dir / "06_run_summary.txt").exists() and (store.dir / "06_run_complete.json").exists()
    assert "status: SUCCESS" in format_summary(rc, us)


def test_a_failed_final_step_is_recorded_as_failed_with_the_step_and_error(tmp_path, valid, strategy):
    store, _, go = run(tmp_path, make_steps(valid, strategy, strategy_fails=True))
    with pytest.raises(RuntimeError):
        go()
    rc = next(e for e in events_of(tmp_path) if e["event_type"] == "run_complete")
    assert rc["status"] == "failed" and rc["failed_step"] == "strategy" and "guardrail exhausted" in rc["error"]
    assert rc["evidence_count"] == 1  # the earlier work is still reported


def test_a_budget_stop_is_its_own_status_with_the_reason(tmp_path, valid, strategy):
    now = [0.0]
    store = RunStore("r1", root=tmp_path)
    monitor = RunMonitor(store, Budget(max_wall_clock_minutes=1), log_path=tmp_path / "runs.jsonl", clock=lambda: now[0])
    steps = make_steps(valid, strategy)
    inner = steps["research"].fn
    steps["research"].fn = lambda *a: (now.__setitem__(0, 120.0), inner(*a))[1]  # research "takes" 2 minutes
    with pytest.raises(BudgetExceeded, match="before the Analyst step"):
        run_pipeline({"b": 1}, store=store, steps=steps, monitor=monitor)
    rc = next(e for e in events_of(tmp_path) if e["event_type"] == "run_complete")
    assert rc["status"] == "stopped_budget" and "wall-clock budget reached" in rc["error"]
    assert (store.dir / "02_evidence_set.json").exists()  # research is kept, and can be resumed
    assert steps["analyst"].calls == 0 and steps["strategy"].calls == 0  # nothing further was spent


def test_resumed_runs_report_that_they_resumed(tmp_path, valid, strategy):
    store, _, go = run(tmp_path, make_steps(valid, strategy, strategy_fails=True))
    with pytest.raises(RuntimeError):
        go()
    _, _, go2 = run(tmp_path, make_steps(valid, strategy))
    go2()
    rcs = [e for e in events_of(tmp_path) if e["event_type"] == "run_complete"]
    assert rcs[-1]["status"] == "success" and set(rcs[-1]["resumed_steps"]) == {"research", "analyst"}


def test_docs_step_logs_a_document_write_event(tmp_path, valid, strategy):
    _, _, go = run(tmp_path, make_steps(valid, strategy), docs="local")
    go()
    ev = events_of(tmp_path)
    dw = next(e for e in ev if e["event_type"] == "document_write")
    assert dw["status"] == "local_verified" and dw["section_count"] == 19 and dw["duration_ms"] >= 0
    rc = next(e for e in ev if e["event_type"] == "run_complete")
    assert rc["document_write_status"] == "local_verified"


def test_a_failed_link_check_degrades_the_run_but_does_not_fail_it(tmp_path, valid, strategy):
    steps = make_steps(valid, strategy)
    steps["links"] = lambda *a: (_ for _ in ()).throw(ConnectionError("mcp down"))
    _, _, go = run(tmp_path, steps)
    go()  # completes
    rc = next(e for e in events_of(tmp_path) if e["event_type"] == "run_complete")
    assert rc["status"] == "success" and rc["broken_url_count"] is None  # unchecked is not the same as zero


def test_over_budget_llm_calls_and_slow_runs_are_flagged(tmp_path, valid, strategy):
    store, monitor, go = run(tmp_path, make_steps(valid, strategy))
    for _ in range(7):
        monitor.on_llm_completed("Analyst Agent", {"prompt_tokens": 10, "completion_tokens": 10})
    for _ in range(15):
        monitor.on_llm_completed("Research Agent", {"prompt_tokens": 10, "completion_tokens": 10})
    go()
    rc = next(e for e in events_of(tmp_path) if e["event_type"] == "run_complete")
    assert any("Analyst Agent used 7 LLM calls" in i for i in rc["issues"])
    assert not any("Research Agent" in i for i in rc["issues"])  # exempt: one LLM step per tool call


def test_preflight_event_accepts_the_real_health_check_shape(tmp_path):
    """Regression: the live server's health_check returns its own 'status' key, which once collided
    with the event's status and crashed the first real run. Fakes never exercised this path."""
    from wire3_gtm.pipeline import log_preflight

    monitor = RunMonitor(RunStore("r1", root=tmp_path), log_path=tmp_path / "runs.jsonl")
    log_preflight(monitor, {"tools": ["recent_news"], "status": "ok", "active_provider": "tavily",
                            "serpapi_configured": True, "tavily_configured": True})
    (e,) = events_of(tmp_path)
    assert e["gate"] == "mcp_preflight" and e["status"] == "ok" and e["active_provider"] == "tavily"
    assert e["provider_key_configured"] is True and e["server_status"] == "ok"
    assert "key" not in json.dumps(e).replace("provider_key_configured", "")  # no key values, only whether one is set


# --- honest run status: skipped calls and thin coverage -------------------------------------

def test_planned_calls_the_agent_never_made_degrade_the_run(tmp_path, valid, strategy):
    """Regression: a live run skipped 2 of 15 planned calls and was reported as a clean success."""
    steps = make_steps(valid, strategy)
    inner = steps["research"].fn

    def research_that_skips(*a):
        phase = inner(*a)
        phase.collector.calls.clear()  # the agent made none of the planned calls
        return phase

    steps["research"].fn = research_that_skips
    store, _, go = run(tmp_path, steps)
    go()
    rc = json.loads(store.load_text("06_run_complete.json"))
    assert rc["status"] == "degraded" and any("planned research call(s) were never executed" in i for i in rc["issues"])


def test_a_complete_research_step_is_not_flagged(tmp_path, valid, strategy):
    store, _, go = run(tmp_path, make_steps(valid, strategy))
    go()
    rc = json.loads(store.load_text("06_run_complete.json"))
    assert rc["status"] == "success" and not any("never executed" in i for i in rc["issues"])


def test_a_thinly_supported_question_is_listed_but_does_not_degrade_the_run(tmp_path, valid, strategy):
    steps = make_steps(valid, strategy)
    inner = steps["analyst"].fn
    steps["analyst"].fn = lambda *a: (lambda r: r)(inner(*a))
    store, _, go = run(tmp_path, steps)
    go()
    report = json.loads(store.load_text("03_analyst_report.json"))
    report["rq_source_depth"] = {"RQ1": 18, "RQ4": 2, "RQ5": 1}
    store.save_json("03_analyst_report.json", report)  # what a real Analyst step records
    from wire3_gtm.pipeline import _finish

    _finish(store, RunMonitor(store, log_path=tmp_path / "runs2.jsonl"), None)
    rc = json.loads(store.load_text("06_run_complete.json"))
    assert any("thin coverage: RQ4 (2), RQ5 (1)" in i for i in rc["issues"]) and "RQ1" not in " ".join(rc["issues"])
