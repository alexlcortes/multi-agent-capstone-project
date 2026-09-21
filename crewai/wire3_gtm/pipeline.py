"""Pipeline: brief -> Head Planner -> Research -> Analyst -> Strategy.

run_pipeline() writes each step's result to a RunStore before the next step
starts, and can resume a failed run from the last step that finished."""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from crewai import Crew, Process

from wire3_gtm.agents import build_agents
from wire3_gtm.analyst_checks import (
    coverage_errors, drop_invented_ids, make_analyst_guardrail, order_by_rq, repair_pricing, repair_theme_rqs,
    rq_checklist, rq_source_depth, source_quality, theme_rq_errors, unsupported_number_flags,
)
from wire3_gtm.analyst_models import AnalystArtifact, check_grounding, dump_contract
from wire3_gtm.strategy_checks import (
    grounding_errors, make_strategy_guardrail, report as strategy_report, unknown_coverage_errors,
)
from wire3_gtm.strategy_models import StrategyArtifact
from wire3_gtm.evidence import EvidenceCollector, EvidenceRecord, EvidenceSet
from wire3_gtm.models import ResearchPlan
from wire3_gtm.evidence import EvidenceCollector as _EC  # noqa: F401
from wire3_gtm.analyst_models import cited_evidence_ids
from wire3_gtm.links import HTTP_URL_RE, check_links, mcp_validator
from wire3_gtm.research_tools import enforce_plan, mcp_preflight, research_tools
from wire3_gtm.run_log import Budget, RunMonitor, activate, flush_events, install_listeners
from wire3_gtm.run_report import build_run_complete, format_summary
from wire3_gtm.run_store import RunStore
from wire3_gtm.tasks import build_tasks

BRIEF_PATH = Path(__file__).parent.parent / "brief.json"


@dataclass
class PhaseResult:
    plan: ResearchPlan
    plan_raw: str  # exactly what CrewAI passes to the next task as context
    evidence: list[EvidenceRecord]
    collector: EvidenceCollector


def _recording_guardrail(store: RunStore | None, step: str, inner: Callable,
                         monitor: RunMonitor | None = None, role: str | None = None) -> Callable:
    """Save every guardrail attempt (raw output + feedback) so a rejected draft is still on
    disk when the retries run out; log each attempt as a retry; and stop the retry loop,
    with a clear reason, if the time or cost budget is spent."""
    if store is None and monitor is None:
        return inner
    attempt = [0]

    def guardrail(output):
        attempt[0] += 1
        if monitor is not None:
            monitor.attempt_seconds(role or step)  # how long the attempt that just finished took
        ok, result = inner(output)
        if store is not None:
            store.save_attempt(step, attempt[0], output.raw, None if ok else result)
        if monitor is not None:
            monitor.note_guardrail(role or step, f"{step}_guardrail", attempt[0], ok, None if ok else str(result))
            if not ok:
                monitor.check_budget(f"after {step} attempt {attempt[0]}")  # raises BudgetExceeded
        return ok, result

    return guardrail


def log_preflight(monitor: RunMonitor, info: dict) -> None:
    """The server's health_check has its own `status` field, so it is nested, not spread into the event."""
    monitor.event("validation_gate", node="MCP Research Tools", gate="mcp_preflight", status="ok",
                  active_provider=info.get("active_provider"), provider_key_configured=info.get(
                      f"{info.get('active_provider')}_configured"), tools=info.get("tools"), server_status=info.get("status"))


def run_planning_and_research(brief: dict, store: RunStore | None = None, monitor: RunMonitor | None = None) -> PhaseResult:
    if monitor is not None:  # fail fast, and record which search provider this run will use
        log_preflight(monitor, mcp_preflight())
    collector = EvidenceCollector(sink=store.path("02_tool_calls.jsonl") if store else None)
    with research_tools(collector, monitor=monitor) as tools:
        agents = build_agents(research_tools=tools)
        tasks = build_tasks(agents)
        phase = {k: tasks[k] for k in ("plan_research", "execute_research")}
        if store:  # persist the plan the moment the Head Planner finishes, before research starts
            phase["plan_research"].callback = lambda out: store.save_text("01_plan.json", out.pydantic.model_dump_json(indent=1)) if out.pydantic else None
        crew = Crew(
            agents=[agents["head_planner"], agents["research"]],
            tasks=list(phase.values()),
            process=Process.sequential,
        )
        crew.kickoff(inputs={"brief": json.dumps(brief)})
        plan = phase["plan_research"].output.pydantic
        if plan is None:
            raise RuntimeError("Head Planner did not return a valid ResearchPlan")
        # The agent is asked to make every planned call and sometimes does not: make the rest here,
        # while the MCP connection is still open.
        enforce_plan(plan, collector, tools, monitor)
    return PhaseResult(plan, phase["plan_research"].output.raw, collector.build_evidence(plan), collector)


def load_brief() -> dict:
    return json.loads(BRIEF_PATH.read_text())


@dataclass
class AnalystResult:
    artifact: AnalystArtifact
    unsupported_numbers: list[dict]  # soft flags for a human, never blocking
    theme_rq_repairs: list[dict]  # themes whose RQs were rewritten from their cited evidence
    source_quality: dict
    dropped_ids: list[dict] = field(default_factory=list)  # invented evidence ids removed (never remapped)
    coverage_gaps: list[str] = field(default_factory=list)  # RQs left uncited because time ran out (run is degraded)
    pricing_repairs: list[dict] = field(default_factory=list)  # rows dropped / guesses nulled in code (see repair_pricing)


def run_analyst(plan: ResearchPlan, evidence: list[EvidenceRecord], store: RunStore | None = None,
                monitor: RunMonitor | None = None) -> AnalystResult:
    """Analyst step. The evidence goes in as an EvidenceSet JSON document and
    comes out as a validated AnalystArtifact; no free text in between."""
    evidence_set = EvidenceSet(run_id=plan.run_id, evidence=order_by_rq(evidence))
    agents = build_agents()
    dropped_log: list[dict] = []
    gaps_log: list[str] = []
    pricing_log: list[dict] = []
    degrade = (lambda: not monitor.can_afford_retry("Analyst Agent")) if monitor is not None else None
    tasks = build_tasks(agents, guardrails={
        "analyze_evidence": _recording_guardrail(
            store, "03_analyst", make_analyst_guardrail(evidence, dropped_log, degrade, gaps_log, pricing_log),
            monitor, "Analyst Agent")})
    task = tasks["analyze_evidence"]
    Crew(agents=[agents["analyst"]], tasks=[task], process=Process.sequential).kickoff(
        inputs={"evidence_set": evidence_set.model_dump_json(),
                "rq_checklist": json.dumps(rq_checklist(plan, evidence), indent=1)}
    )
    artifact = task.output.pydantic
    if artifact is None:
        raise RuntimeError("Analyst did not return a valid AnalystArtifact")
    # Belt and braces: the guardrail retries a bounded number of times, so
    # re-run the hard checks on whatever came back.
    valid_ids = {e.evidence_id for e in evidence}
    artifact, dropped = drop_invented_ids(artifact, valid_ids)  # idempotent after the guardrail's pass
    dropped_ids = dropped or list(dropped_log)
    theme_rq_repairs = repair_theme_rqs(artifact, evidence)
    coverage_gaps = list(gaps_log)  # accepted on purpose because time ran out; anything else must still be clean
    errors = check_grounding(artifact, valid_ids) + theme_rq_errors(artifact, evidence) + (
        [] if coverage_gaps else coverage_errors(artifact, evidence))
    if errors:
        raise RuntimeError(f"Analyst artifact failed hard checks after retries: {errors[:5]}")
    return AnalystResult(
        artifact=artifact,
        unsupported_numbers=unsupported_number_flags(artifact, evidence),
        theme_rq_repairs=theme_rq_repairs,
        source_quality=source_quality(artifact, evidence),
        dropped_ids=dropped_ids,
        coverage_gaps=coverage_gaps,
        pricing_repairs=list(pricing_log),
    )


@dataclass
class StrategyResult:
    artifact: StrategyArtifact
    report: dict  # basis mix and suspect Wire3-response claims


def run_strategy(analyst: AnalystArtifact, store: RunStore | None = None,
                 monitor: RunMonitor | None = None) -> StrategyResult:
    """Strategy step. The Analyst's tables, themes and ids go in as its own JSON
    contract (dump_contract, so it matches analyst_artifact.schema.json exactly)
    and come out as a validated StrategyArtifact."""
    agents = build_agents()
    tasks = build_tasks(agents, guardrails={"build_strategy": _recording_guardrail(
            store, "04_strategy", make_strategy_guardrail(analyst), monitor, "Strategy Agent")})
    task = tasks["build_strategy"]
    Crew(agents=[agents["strategy"]], tasks=[task], process=Process.sequential).kickoff(
        inputs={"analyst_artifact": json.dumps(dump_contract(analyst))}
    )
    artifact = task.output.pydantic
    if artifact is None:
        raise RuntimeError("Strategy did not return a valid StrategyArtifact")
    errors = grounding_errors(artifact, analyst) + unknown_coverage_errors(artifact, analyst)
    if errors:
        raise RuntimeError(f"Strategy artifact failed hard checks after retries: {errors[:5]}")
    return StrategyResult(artifact=artifact, report=strategy_report(artifact))


@dataclass
class PipelineResult:
    run_id: str
    run_dir: Path
    plan: ResearchPlan
    evidence: list[EvidenceRecord]
    analyst: AnalystArtifact
    strategy: StrategyArtifact


def check_cited_links(store: RunStore, analyst: AnalystArtifact, evidence: list[EvidenceRecord]) -> dict:
    """HEAD-check every source the analysis cites, through the MCP validate_source tool."""
    by_id = {e.evidence_id: e for e in evidence}
    urls = [by_id[i].source_url for i in cited_evidence_ids(analyst.model_dump()) if i in by_id]
    with mcp_validator() as validate:
        return check_links(urls, validate)


def _load(store: RunStore, name: str, model=None):
    if not store.exists(name):
        return None
    text = store.load_text(name)
    return model.model_validate_json(text) if model else json.loads(text)


@dataclass
class RunContext:
    """Everything a pipeline step needs. The Flow (wire3_gtm/flow.py) holds the artifacts as typed state."""

    brief: dict
    store: RunStore
    monitor: RunMonitor
    fns: dict
    docs: str | None = None
    doc_client: object = None


def step_research(ctx: RunContext) -> tuple[ResearchPlan, list[EvidenceRecord]]:
    store, monitor = ctx.store, ctx.monitor
    if store.exists("01_plan.json") and store.exists("02_evidence_set.json"):
        plan = _load(store, "01_plan.json", ResearchPlan)
        evidence = _load(store, "02_evidence_set.json", EvidenceSet).evidence
        monitor.run_id = plan.run_id
        store.mark_resumed("research")
        return plan, evidence
    with store.step("research"):
        phase = ctx.fns["research"](ctx.brief, store, monitor)
        plan, evidence = phase.plan, phase.evidence
        monitor.run_id = plan.run_id
        store.save_text("01_plan.json", plan.model_dump_json(indent=1))
        store.save_text("02_evidence_set.json",
                        EvidenceSet(run_id=plan.run_id, evidence=evidence).model_dump_json(indent=1))
        coverage = phase.collector.coverage(plan)
        answered = len({e.research_question_id for e in evidence if e.research_question_id})
        store.save_json("02_research_report.json", {**coverage, "research_questions_answered": answered,
                                                    "research_questions_total": len(plan.research_questions)})
        for call, rqs in zip(phase.collector.calls, phase.collector.match_plan(plan)):
            monitor.tool_attempts += call.attempts
            monitor.tool_retries += max(call.attempts - 1, 0)
            monitor.event("tool_call", node="MCP Research Tools", tool=call.tool, args=call.args,
                          status=call.status, attempts=call.attempts, duration_ms=call.duration_ms,
                          matched_planned_call=bool(rqs), research_question_ids=rqs,
                          evidence_produced=len(call.results), error=call.error,
                          attempt_errors=call.attempt_errors or None)
        monitor.event("validation_gate", node="Research Agent", gate="research_coverage",
                      status="ok" if not coverage["failed"] else "degraded", evidence_count=len(evidence),
                      research_questions_answered=answered,
                      research_questions_total=len(plan.research_questions), **coverage)
        store.note(plan_run_id=plan.run_id)
    return plan, evidence


def step_analyst(ctx: RunContext, plan: ResearchPlan, evidence: list[EvidenceRecord]) -> AnalystArtifact:
    store, monitor = ctx.store, ctx.monitor
    if store.exists("03_analyst_artifact.json"):
        store.mark_resumed("analyst")
        return _load(store, "03_analyst_artifact.json", AnalystArtifact)
    monitor.check_budget("before the Analyst step")
    with store.step("analyst"):
        result = ctx.fns["analyst"](plan, evidence, store, monitor)
        analyst = result.artifact
        store.save_json("03_analyst_artifact.json", dump_contract(analyst))
        store.save_json("03_analyst_report.json", {
            "unsupported_numbers": result.unsupported_numbers,
            "theme_rq_repairs": result.theme_rq_repairs,
            "source_quality": result.source_quality,
            "dropped_ids": result.dropped_ids,
            "coverage_gaps": result.coverage_gaps,
            "rq_source_depth": rq_source_depth(analyst, evidence),
            "pricing_repairs": result.pricing_repairs,
        })
        monitor.event("validation_gate", node="Analyst Agent", gate="analyst_grounding", status="ok",
                      dropped_id_count=len(result.dropped_ids), theme_rq_repairs=len(result.theme_rq_repairs),
                      unsupported_number_count=len(result.unsupported_numbers),
                      coverage_gaps=len(result.coverage_gaps), pricing_repairs=len(result.pricing_repairs),
                      top_tier_share=result.source_quality.get("top_tier_share"))
    return analyst


def step_strategy(ctx: RunContext, analyst: AnalystArtifact) -> StrategyArtifact:
    store, monitor = ctx.store, ctx.monitor
    if store.exists("04_strategy_artifact.json"):
        store.mark_resumed("strategy")
        return _load(store, "04_strategy_artifact.json", StrategyArtifact)
    monitor.check_budget("before the Strategy step")
    with store.step("strategy"):
        result = ctx.fns["strategy"](analyst, store, monitor)
        strategy = result.artifact
        store.save_json("04_strategy_artifact.json", dump_contract(strategy))
        store.save_json("04_strategy_report.json", result.report)
        monitor.event("validation_gate", node="Strategy Agent", gate="strategy_grounding",
                      status="ok" if not result.report.get("wire3_response_claims_evidence") else "degraded",
                      **result.report)
    return strategy


def step_links(ctx: RunContext, analyst: AnalystArtifact, evidence: list[EvidenceRecord]) -> dict:
    """Validate the cited sources. A failure here never fails the run: it is reported as unchecked."""
    store, monitor = ctx.store, ctx.monitor
    if store.exists("06_link_check.json"):
        return _load(store, "06_link_check.json")
    try:
        links = ctx.fns["links"](store, analyst, evidence)
    except Exception as exc:  # noqa: BLE001
        links = {"status": "unchecked", "error": f"{type(exc).__name__}: {exc}"[:300], "urls_total": None,
                 "invalid_url_count": None, "broken_url_count": None, "blocked": None}
    store.save_json("06_link_check.json", links)
    monitor.event("validation_gate", node="Link check", gate="link_check",
                  status=("degraded" if links.get("broken_url_count") else
                          "unchecked" if links.get("status") == "unchecked" else "ok"),
                  check=links.get("status", "checked"),
                  **{k: v for k, v in links.items()
                     if k not in ("broken", "blocked_urls", "malformed", "status", "unverified_urls")})
    return links


def step_docs(ctx: RunContext) -> dict | None:
    """Docs Writer: an optional final milestone (ctx.docs is "local", "google" or None)."""
    if not ctx.docs:
        return None
    return run_docs(ctx.store, ctx.docs, client=ctx.doc_client, monitor=ctx.monitor)


def run_pipeline(
    brief: dict,
    run_id: str | None = None,
    store: RunStore | None = None,
    steps: dict[str, Callable] | None = None,
    docs: str | None = None,
    doc_client=None,
    monitor: RunMonitor | None = None,
) -> PipelineResult:
    """Run the pipeline as a CrewAI Flow (wire3_gtm/flow.py), saving each result before the next
    step begins, and log the run.

    Passing the run_id of an earlier run resumes it: steps whose artifact is already on
    disk are loaded, not re-run. `docs` ("local" or "google") adds the Docs Writer as a
    final milestone. `steps` lets tests swap the LLM/network steps.

    A run_complete + usage_summary record is written to logs/runs.jsonl however the run
    ends (success, degraded, failed, or stopped by a budget limit)."""
    from wire3_gtm.flow import Wire3Flow

    store = store or RunStore(run_id)
    monitor = monitor or RunMonitor(store, Budget.from_brief(brief))
    fns = {"research": run_planning_and_research, "analyst": run_analyst, "strategy": run_strategy,
           "links": check_cited_links, **(steps or {})}
    ctx = RunContext(brief, store, monitor, fns, docs, doc_client)
    install_listeners()
    activate(monitor)
    error: BaseException | None = None
    try:
        if not store.exists("00_brief.json"):
            store.save_json("00_brief.json", brief)
        monitor.event("pipeline_start", node="Pipeline", status="ok", docs_backend=docs, orchestrator="crewai.Flow",
                      resuming=[n for n in ("01_plan.json", "02_evidence_set.json", "03_analyst_artifact.json",
                                            "04_strategy_artifact.json") if store.exists(n)],
                      budget=monitor.budget.__dict__)
        flow = Wire3Flow(ctx)
        flow.kickoff()
        s = flow.state
        return PipelineResult(store.run_id, store.dir, s.plan, s.evidence, s.analyst, s.strategy)
    except BaseException as exc:
        error = exc
        raise
    finally:
        _finish(store, monitor, error)


def _finish(store: RunStore, monitor: RunMonitor, error: BaseException | None) -> None:
    """Write run_complete + usage_summary from what is on disk. Never masks the original error."""
    try:
        flush_events()
        analyst = _load(store, "03_analyst_artifact.json")
        rc = build_run_complete(
            monitor, store, plan=_load(store, "01_plan.json", ResearchPlan),
            evidence=(_load(store, "02_evidence_set.json", EvidenceSet) or EvidenceSet(run_id="", evidence=[])).evidence
            if store.exists("02_evidence_set.json") else None,
            coverage=_load(store, "02_research_report.json"), analyst_report=_load(store, "03_analyst_report.json"),
            strategy_report=_load(store, "04_strategy_report.json"), link_report=_load(store, "06_link_check.json"),
            doc_result=_load(store, "05_document.json"),
            analyst_brief_id=analyst.get("brief_id") if analyst else None, error=error)
        monitor.event("run_complete", **rc)
        usage = monitor.usage_summary(rc["status"])
        store.save_json("06_run_complete.json", rc)
        store.save_text("06_run_summary.txt", format_summary(rc, usage) + "\n")
    except Exception as exc:  # noqa: BLE001 -- logging must not hide the real failure
        print(f"warning: could not write run_complete: {type(exc).__name__}: {exc}")
    finally:
        activate(None)


def salvage_research(store: RunStore) -> Path:
    """Recover a Research step that crashed part-way, with no new searches: rebuild the evidence
    set from the tool calls already on disk (02_tool_calls.jsonl, written as each search returned)
    and the plan (01_plan.json, written the moment the Head Planner finished).

    The result may be PARTIAL: it is marked salvaged, and the coverage report says how many of the
    planned calls actually ran, so the run is reported as degraded and not as a clean success."""
    if not (store.exists("01_plan.json") and store.exists("02_tool_calls.jsonl")):
        raise RuntimeError("nothing to salvage: need 01_plan.json and 02_tool_calls.jsonl")
    plan = ResearchPlan.model_validate_json(store.load_text("01_plan.json"))
    collector = EvidenceCollector.from_jsonl(store.path("02_tool_calls.jsonl"))
    evidence = collector.build_evidence(plan)
    if not evidence:
        raise RuntimeError("the saved tool calls contain no evidence; rerun the research step")
    store.save_text("02_evidence_set.json", EvidenceSet(run_id=plan.run_id, evidence=evidence).model_dump_json(indent=1))
    coverage = collector.coverage(plan)
    answered = len({e.research_question_id for e in evidence if e.research_question_id})
    store.save_json("02_research_report.json", {**coverage, "research_questions_answered": answered,
                                                "research_questions_total": len(plan.research_questions),
                                                "salvaged_from": "02_tool_calls.jsonl"})
    store.mark_salvaged("research", tool_calls_recovered=coverage["executed"], tool_calls_planned=coverage["planned"])
    return store.path("02_evidence_set.json")


def salvage_analyst(store: RunStore) -> Path:
    """Recover a failed Analyst step from the drafts kept in attempts/, with no
    LLM call: newest draft first, drop invented ids, repair theme RQs, then
    apply the same hard checks as the guardrail. Raises if no draft passes."""
    import json as _json
    from pydantic import ValidationError

    evidence = EvidenceSet.model_validate_json(store.load_text("02_evidence_set.json")).evidence
    valid_ids = {e.evidence_id for e in evidence}
    drafts = sorted((store.dir / "attempts").glob("03_analyst_attempt_*.json"),
                    key=lambda p: int(p.stem.rsplit("_", 1)[1]), reverse=True)
    rejected = []
    for path in drafts:
        raw = _json.loads(path.read_text())["raw"]
        try:
            doc, pricing_repairs = repair_pricing(_json.loads(raw))  # the same repairs the guardrail applies
            artifact = AnalystArtifact.model_validate(doc)
        except (ValidationError, ValueError, TypeError) as exc:
            rejected.append(f"{path.name}: not a valid AnalystArtifact ({getattr(exc, 'error_count', lambda: 1)()} errors)")
            continue
        artifact, dropped = drop_invented_ids(artifact, valid_ids)
        repairs = repair_theme_rqs(artifact, evidence)
        errors = check_grounding(artifact, valid_ids) + coverage_errors(artifact, evidence)
        if errors:
            rejected.append(f"{path.name}: {errors[0]}")
            continue
        store.save_json("03_analyst_artifact.json", dump_contract(artifact))
        store.save_json("03_analyst_report.json", {
            "salvaged_from": path.name,
            "dropped_ids": dropped,
            "pricing_repairs": pricing_repairs,
            "theme_rq_repairs": repairs,
            "unsupported_numbers": unsupported_number_flags(artifact, evidence),
            "source_quality": source_quality(artifact, evidence),
        })
        store.mark_salvaged("analyst", salvaged_from=path.name, dropped_id_count=len(dropped))
        return store.path("03_analyst_artifact.json")
    raise RuntimeError("no saved Analyst draft passes the hard checks: " + " | ".join(rejected))


def _run_docs(store: RunStore, backend: str = "local", client=None) -> dict:
    """Docs Writer milestone. Reads the run's saved, validated artifacts, so it
    can be run (or re-run) against any finished run without touching the LLM steps.

    backend "local":  build the document, save Markdown, and verify our own
                      Docs requests against a simulated read-back. No Google.
    backend "google": also create the Google Doc, write it, read it back,
                      verify it, and export a PDF.

    Idempotent: the document id is saved the moment the Doc exists, and a resume
    verifies that document instead of creating a second one."""
    from wire3_gtm import docs_google
    from wire3_gtm.docs_content import DocsContentError, build_document, render_markdown, to_docs_requests

    if backend not in ("local", "google"):
        raise ValueError(f"unknown docs backend {backend!r}")
    for name in ("01_plan.json", "02_evidence_set.json", "03_analyst_artifact.json", "04_strategy_artifact.json"):
        if not store.exists(name):
            raise DocsContentError(f"cannot write a document: {name} is missing; finish the text pipeline first")

    with store.step("docs"):
        plan = ResearchPlan.model_validate_json(store.load_text("01_plan.json"))
        evidence = EvidenceSet.model_validate_json(store.load_text("02_evidence_set.json")).evidence
        analyst = AnalystArtifact.model_validate_json(store.load_text("03_analyst_artifact.json"))
        strategy = StrategyArtifact.model_validate_json(store.load_text("04_strategy_artifact.json"))
        report = json.loads(store.load_text("03_analyst_report.json")) if store.exists("03_analyst_report.json") else None

        doc_plan = build_document(strategy, analyst, evidence, plan, store.run_id, analyst_report=report)
        requests = to_docs_requests(doc_plan)
        store.save_text("05_document.md", render_markdown(doc_plan))
        store.save_json("05_document_requests.json", requests)

        # our own requests, applied to an empty document in UTF-16 units: catches index bugs offline
        local_errors = docs_google.verify(doc_plan.expected, docs_google.simulate_docs(doc_plan, requests))
        if local_errors:
            raise DocsContentError("document failed the local check: " + " | ".join(local_errors[:5]))
        result = {"backend": backend, "title": doc_plan.title, "sections": len(doc_plan.sections),
                  "sources": len(doc_plan.sources), "status": "local_verified", "document_url": None}

        if backend == "google":
            client = client or docs_google.GoogleDocs()
            prior = json.loads(store.load_text("05_document.json")) if store.exists("05_document.json") else {}
            doc_id = prior.get("document_id")
            if doc_id is None:
                doc_id = client.create(doc_plan.title)
                store.save_json("05_document.json", {**result, "document_id": doc_id, "status": "created",
                                                     "document_url": docs_google.url_for(doc_id)})
                client.write(doc_id, requests)  # single atomic batchUpdate, deliberately not retried
                store.save_json("05_document.json", {**result, "document_id": doc_id, "status": "written",
                                                     "document_url": docs_google.url_for(doc_id)})
            doc = client.read(doc_id)
            errors = docs_google.verify(doc_plan.expected, doc)
            if errors:
                raise DocsContentError(
                    f"Google Doc {docs_google.url_for(doc_id)} failed the post-write check: " + " | ".join(errors[:8]))
            pdf = client.export_pdf(doc_id)
            store.path("05_document.pdf").write_bytes(pdf)
            result.update(status="verified", document_id=doc_id, document_url=docs_google.url_for(doc_id),
                          pdf_bytes=len(pdf))
        store.save_json("05_document.json", result)
    return result


def run_docs(store: RunStore, backend: str = "local", client=None, monitor: RunMonitor | None = None) -> dict:
    """Docs Writer milestone with logging: agent_start, then a document_write event that records
    the outcome (verified / local_verified / error), duration, and the document id and url."""
    import time

    t0 = time.time()
    if monitor is not None:
        monitor.event("agent_start", node="Docs Writer", agent="Docs Writer", status="ok", model=None, provider="google")
    try:
        result = _run_docs(store, backend, client)
    except BaseException as exc:
        if monitor is not None:
            monitor.event("document_write", node="Docs Writer", status="error",
                          duration_ms=int((time.time() - t0) * 1000), error=f"{type(exc).__name__}: {exc}"[:500])
        raise
    if monitor is not None:
        monitor.event("document_write", node="Docs Writer", status=result["status"],
                      duration_ms=int((time.time() - t0) * 1000), backend=result["backend"],
                      document_id=result.get("document_id"), document_url=result.get("document_url"),
                      section_count=result["sections"], source_count=result["sources"],
                      pdf_bytes=result.get("pdf_bytes"))
    return result
