from wire3_gtm.compare_logs import latest_run, render


def rc(impl, run, status="success", **kw):
    return {"event_type": "run_complete", "implementation": impl, "client_run_id": run, "status": status,
            "duration_ms": 540000, "research_questions_answered": 8, "research_questions_total": 8, "evidence_count": 400,
            "tool_calls_executed": 14, "tool_calls_planned": 14, "budget_max_cost_usd": 2.5,
            "document_write_status": "verified", "issues": [], **kw}


def us(impl, run, cost):
    return {"event_type": "usage_summary", "implementation": impl, "client_run_id": run, "estimated_llm_cost_usd": cost,
            "total_prompt_tokens": 1000, "total_completion_tokens": 200, "total_reasoning_tokens": 120,
            "per_agent": [{"agent": "Analyst Agent", "llm_calls": 2}]}


def test_latest_run_skips_failed_runs_and_pairs_usage_by_run_id():
    events = [rc("crewai", "a"), us("crewai", "a", 0.1), rc("crewai", "b", status="failed"), us("crewai", "b", 9.9)]
    run, usage = latest_run(events)
    assert run["client_run_id"] == "a" and usage["estimated_llm_cost_usd"] == 0.1
    assert latest_run([rc("crewai", "b", status="failed")]) == (None, None)


def test_the_comparison_states_how_the_two_record_things_differently():
    n = (rc("n8n", "n", rate_limit_errors_logged=0, invalid_url_count=0), us("n8n", "n", 0.19))
    c = (rc("crewai", "c", latency_within_budget=True, latency_within_kpi=True, broken_url_count=0, invalid_url_count=0,
            retries={"total": 3}), us("crewai", "c", 0.31))
    out = render(n, c)
    assert "LOWER BOUND" in out and "includes reasoning tokens" in out
    assert "approximate" in out and "3 counted" in out and "HEAD-checked" in out and "format check only" in out
