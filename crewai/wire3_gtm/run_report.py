"""The run_complete event (n8n's fields plus the guide's Step 8 list) and a readable summary."""

from wire3_gtm.agents import model as current_model
from wire3_gtm.run_log import PROVIDER, BudgetExceeded, RunMonitor
from wire3_gtm.run_store import RunStore

KPI_LATENCY_MIN = 15  # guide Step 10: brief to drafted GTM document in under 15 minutes
KPI_TOP_TIER = 0.80
MIN_SOURCES_PER_RQ = 3  # below this a question counts as covered but thinly supported (reported, not blocking)


def build_run_complete(monitor: RunMonitor, store: RunStore, *, plan=None, evidence=None, coverage=None,
                       analyst_report=None, strategy_report=None, link_report=None, doc_result=None,
                       analyst_brief_id: str | None = None, error: BaseException | None = None) -> dict:
    manifest = store.manifest()
    steps = manifest["steps"]
    issues, degraded = [], False
    rq_total = len(plan.research_questions) if plan else None
    answered = len({e.research_question_id for e in (evidence or []) if e.research_question_id})
    coverage = coverage or {}
    strategy_clean = None if strategy_report is None else not strategy_report.get("wire3_response_claims_evidence")

    # Calls the plan named but the Research Agent never made. `executed` counts every recorded call,
    # planned or not, so subtract the unplanned ones. (A live run skipped 2 of 15 and was reported success.)
    never_run = (coverage.get("planned") or 0) - ((coverage.get("executed") or 0) - (coverage.get("unplanned") or 0))
    if never_run > 0:
        issues.append(f"{never_run} of {coverage['planned']} planned research call(s) were never executed"); degraded = True
    if coverage.get("enforced"):  # recovered: the pipeline made the calls, so this is information, not a failure
        issues.append(f"the Research Agent skipped {coverage['enforced']} planned call(s); the pipeline ran them")
    if coverage.get("failed"):
        issues.append(f"{coverage['failed']} research tool call(s) failed after retries"); degraded = True
    if any(s.get("status") == "salvaged" for s in steps.values()):
        issues.append("a step was recovered from a saved draft (salvage)"); degraded = True
    if link_report and link_report.get("broken_url_count"):
        issues.append(f"{link_report['broken_url_count']} cited link(s) are broken"); degraded = True
    if link_report and link_report.get("invalid_url_count"):
        issues.append(f"{link_report['invalid_url_count']} malformed source URL(s)"); degraded = True
    if analyst_report and analyst_report.get("coverage_gaps"):
        issues.append(f"the Analyst was accepted with {len(analyst_report['coverage_gaps'])} coverage gap(s) "
                      "(uncited research questions or unused archived pricing pages) because the time budget left no room for another attempt"); degraded = True
    if analyst_report:
        q = analyst_report.get("source_quality") or {}
        if q and q.get("top_tier_share", 1) < KPI_TOP_TIER:
            issues.append(f"source quality {q['top_tier_share']:.0%} top-tier is below the {KPI_TOP_TIER:.0%} KPI target")
        for key, label in (("pricing_repairs", "pricing row(s) repaired in code (a Wire3 row dropped or an inferred price nulled)"),
                           ("unsupported_numbers", "number(s) marked evidence not found in the cited text"),
                           ("dropped_ids", "invented evidence id(s) removed")):
            if analyst_report.get(key):
                issues.append(f"{len(analyst_report[key])} {label}")
    for role, u in monitor.usage.items():  # Research is exempt: one LLM step per tool call (SETUP_DECISIONS)
        if role != "Research Agent" and u.llm_calls > monitor.budget.max_llm_calls_per_role:
            issues.append(f"{role} used {u.llm_calls} LLM calls (brief cap {monitor.budget.max_llm_calls_per_role} per role)")
    depth = (analyst_report or {}).get("rq_source_depth") or {}
    thin = sorted(f"{rq} ({n})" for rq, n in depth.items() if n < MIN_SOURCES_PER_RQ)
    if thin:  # per-source coverage is lenient by design, so make a thinly supported question visible
        issues.append(f"thin coverage: {', '.join(thin)} backed by fewer than {MIN_SOURCES_PER_RQ} distinct cited sources")
    minutes = monitor.elapsed_s() / 60
    if minutes > monitor.budget.max_wall_clock_minutes:
        issues.append(f"took {minutes:.1f} min, over the {monitor.budget.max_wall_clock_minutes} min budget"); degraded = True
    if monitor.search_calls >= monitor.budget.max_search_calls:
        issues.append("search-call budget fully used"); degraded = True
    if monitor.cost_usd() > monitor.budget.max_cost_usd:
        issues.append(f"cost ${monitor.cost_usd():.3f} is over the ${monitor.budget.max_cost_usd} budget"); degraded = True

    if flags := (strategy_report or {}).get("equity_flags"):
        issues.append(f"{len(flags)} strategy statement(s) flagged by the equity screen; review them before use "
                      f"(document section 'Equity and compliance check')"); degraded = True
    if same := (strategy_report or {}).get("icps_with_identical_channels"):
        issues.append(f"customer profiles {', '.join(same)} are reached by exactly the same channels")
    mix = (strategy_report or {}).get("basis_mix") or {}
    if mix.get("inference"):  # allowed, but never silent: each is marked [inference] in the document too
        issues.append(f"{mix['inference']} of {sum(mix.values())} strategy claims have no source (basis inference)")
    if monitor.budget_overrides:  # a test run with tightened limits: never mistaken for a normal one
        issues.append("budget overridden for this run: " + ", ".join(f"{k}={v}" for k, v in monitor.budget_overrides.items()))
        degraded = True
    if error is None:
        status = "degraded" if degraded else "success"
    elif isinstance(error, BudgetExceeded):
        status = "stopped_budget"
    else:
        status = "failed"
    failed_step = next((k for k, v in steps.items() if v.get("status") == "failed"), None)

    return {
        "node": "Pipeline", "status": status,
        "brief_id": analyst_brief_id, "plan_run_id": plan.run_id if plan else None,
        "duration_ms": int(monitor.elapsed_s() * 1000),
        "step_seconds": {k: v.get("seconds") for k, v in steps.items()},
        "resumed_steps": [k for k, v in steps.items() if v.get("status") in ("resumed", "salvaged")],
        "latency_within_budget": minutes <= monitor.budget.max_wall_clock_minutes,
        "latency_within_kpi": minutes < KPI_LATENCY_MIN,
        "model": current_model(), "provider": PROVIDER,
        "research_questions_answered": answered if plan else None, "research_questions_total": rq_total,
        "evidence_coverage_percent": round(100 * answered / rq_total) if rq_total else None,
        "evidence_count": len(evidence) if evidence is not None else None,
        "linked_sources_cited": (link_report or {}).get("urls_total"),
        "invalid_url_count": (link_report or {}).get("invalid_url_count"),
        "broken_url_count": (link_report or {}).get("broken_url_count"),
        "blocked_url_count": (link_report or {}).get("blocked"),
        "unverified_url_count": (link_report or {}).get("unverified"),
        "tool_calls_planned": coverage.get("planned"), "tool_calls_executed": coverage.get("executed"),
        "tool_calls_failed": coverage.get("failed"), "tool_calls_retried": coverage.get("retried"),
        "retries": monitor.retries(),
        "estimated_llm_cost_usd": round(monitor.cost_usd(), 5), "cost_is_lower_bound": False,
        "search_calls_attempted": monitor.search_calls,
        "strategy_grounding_clean": strategy_clean,
        "document_write_status": (doc_result or {}).get("status", "not_run"),
        "document_url": (doc_result or {}).get("document_url"),
        "budget_max_wall_clock_minutes": monitor.budget.max_wall_clock_minutes,
        "budget_max_search_calls": monitor.budget.max_search_calls,
        "budget_max_cost_usd": monitor.budget.max_cost_usd,
        "failed_step": failed_step, "error": (f"{type(error).__name__}: {error}"[:500] if error else None),
        "run_dir": str(store.dir), "issues": issues,
    }


def format_summary(rc: dict, usage: dict | None = None) -> str:
    """Plain text for a terminal screenshot. Contains no credentials, keys or tokens."""
    mins = rc["duration_ms"] / 60000
    lines = [
        f"CrewAI run {rc['run_dir'].rsplit('/', 1)[-1]}   status: {rc['status'].upper()}",
        f"  latency        {mins:.1f} min   (budget {rc['budget_max_wall_clock_minutes']} min, KPI < {KPI_LATENCY_MIN} min)",
        "  step seconds   " + ", ".join(f"{k} {v}s" for k, v in rc["step_seconds"].items() if v is not None),
        f"  research Qs    {rc['research_questions_answered']}/{rc['research_questions_total']} answered "
        f"({rc['evidence_coverage_percent']}%),  {rc['evidence_count']} evidence records",
        f"  tool calls     {rc['tool_calls_executed']}/{rc['tool_calls_planned']} executed, "
        f"{rc['tool_calls_failed']} failed, {rc['tool_calls_retried']} retried",
        f"  retries        {rc['retries']['total']} total  {rc['retries']}",
        f"  LLM cost       ${rc['estimated_llm_cost_usd']:.4f}  (provider-reported tokens, reasoning included; "
        f"search fees excluded)   budget ${rc['budget_max_cost_usd']}",
    ]
    if usage:
        for a in usage["per_agent"]:
            lines.append(f"    {a['agent']:15} {a['llm_calls']:>2} calls  {a['prompt_tokens']:>7} in  "
                         f"{a['completion_tokens']:>6} out ({a['reasoning_tokens']} reasoning)  ${a['cost_usd']:.4f}")
    lines += [
        f"  links          {rc['linked_sources_cited']} cited, {rc['broken_url_count']} broken, "
        f"{rc['blocked_url_count']} bot-blocked, {rc['unverified_url_count']} unverified (timeouts), "
        f"{rc['invalid_url_count']} malformed",
        f"  document       {rc['document_write_status']}   {rc['document_url'] or ''}",
    ]
    lines += [f"  issue          {i}" for i in rc["issues"]] or ["  issues         none"]
    if rc["error"]:
        lines.append(f"  ERROR          {rc['error']}  (failed step: {rc['failed_step']})")
    return "\n".join(lines)
