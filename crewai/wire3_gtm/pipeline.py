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
    coverage_errors, drop_invented_ids, make_analyst_guardrail, repair_theme_rqs, source_quality,
    theme_rq_errors, unsupported_number_flags,
)
from wire3_gtm.analyst_models import AnalystArtifact, check_grounding, dump_contract
from wire3_gtm.strategy_checks import (
    grounding_errors, make_strategy_guardrail, report as strategy_report, unknown_coverage_errors,
)
from wire3_gtm.strategy_models import StrategyArtifact
from wire3_gtm.evidence import EvidenceCollector, EvidenceRecord, EvidenceSet
from wire3_gtm.models import ResearchPlan
from wire3_gtm.research_tools import research_tools
from wire3_gtm.run_store import RunStore
from wire3_gtm.tasks import build_tasks

BRIEF_PATH = Path(__file__).parent.parent / "brief.json"


@dataclass
class PhaseResult:
    plan: ResearchPlan
    plan_raw: str  # exactly what CrewAI passes to the next task as context
    evidence: list[EvidenceRecord]
    collector: EvidenceCollector


def _recording_guardrail(store: RunStore | None, step: str, inner: Callable) -> Callable:
    """Save every guardrail attempt (raw output + feedback) so a rejected draft
    is still on disk when the retries run out."""
    if store is None:
        return inner
    attempt = [0]

    def guardrail(output):
        attempt[0] += 1
        ok, result = inner(output)
        store.save_attempt(step, attempt[0], output.raw, None if ok else result)
        return ok, result

    return guardrail


def run_planning_and_research(brief: dict, store: RunStore | None = None) -> PhaseResult:
    collector = EvidenceCollector(sink=store.path("02_tool_calls.jsonl") if store else None)
    with research_tools(collector) as tools:
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


def run_analyst(plan: ResearchPlan, evidence: list[EvidenceRecord], store: RunStore | None = None) -> AnalystResult:
    """Analyst step. The evidence goes in as an EvidenceSet JSON document and
    comes out as a validated AnalystArtifact; no free text in between."""
    evidence_set = EvidenceSet(run_id=plan.run_id, evidence=evidence)
    agents = build_agents()
    dropped_log: list[dict] = []
    tasks = build_tasks(agents, guardrails={
        "analyze_evidence": _recording_guardrail(store, "03_analyst", make_analyst_guardrail(evidence, dropped_log))})
    task = tasks["analyze_evidence"]
    Crew(agents=[agents["analyst"]], tasks=[task], process=Process.sequential).kickoff(
        inputs={"evidence_set": evidence_set.model_dump_json()}
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
    errors = check_grounding(artifact, valid_ids) + theme_rq_errors(artifact, evidence) + coverage_errors(artifact, evidence)
    if errors:
        raise RuntimeError(f"Analyst artifact failed hard checks after retries: {errors[:5]}")
    return AnalystResult(
        artifact=artifact,
        unsupported_numbers=unsupported_number_flags(artifact, evidence),
        theme_rq_repairs=theme_rq_repairs,
        source_quality=source_quality(artifact, evidence),
        dropped_ids=dropped_ids,
    )


@dataclass
class StrategyResult:
    artifact: StrategyArtifact
    report: dict  # basis mix and suspect Wire3-response claims


def run_strategy(analyst: AnalystArtifact, store: RunStore | None = None) -> StrategyResult:
    """Strategy step. The Analyst's tables, themes and ids go in as its own JSON
    contract (dump_contract, so it matches analyst_artifact.schema.json exactly)
    and come out as a validated StrategyArtifact."""
    agents = build_agents()
    tasks = build_tasks(agents, guardrails={"build_strategy": _recording_guardrail(store, "04_strategy", make_strategy_guardrail(analyst))})
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


def run_pipeline(
    brief: dict,
    run_id: str | None = None,
    store: RunStore | None = None,
    steps: dict[str, Callable] | None = None,
) -> PipelineResult:
    """Run every step, saving each result before the next begins.

    Passing the run_id of an earlier run resumes it: steps whose artifact is
    already on disk are loaded, not re-run, so a failure in Strategy never
    repeats the searches or the Analyst. `steps` lets tests swap the LLM steps.
    """
    store = store or RunStore(run_id)
    fns = {"research": run_planning_and_research, "analyst": run_analyst, "strategy": run_strategy, **(steps or {})}
    if not store.exists("00_brief.json"):
        store.save_json("00_brief.json", brief)

    # 1. planning + research
    if store.exists("01_plan.json") and store.exists("02_evidence_set.json"):
        plan = ResearchPlan.model_validate_json(store.load_text("01_plan.json"))
        evidence = EvidenceSet.model_validate_json(store.load_text("02_evidence_set.json")).evidence
        store.mark_resumed("research")
    else:
        with store.step("research"):
            phase = fns["research"](brief, store)
            plan, evidence = phase.plan, phase.evidence
            store.save_text("01_plan.json", plan.model_dump_json(indent=1))
            store.save_text("02_evidence_set.json", EvidenceSet(run_id=plan.run_id, evidence=evidence).model_dump_json(indent=1))
            store.save_json("02_research_report.json", phase.collector.coverage(plan))
            store.note(plan_run_id=plan.run_id)

    # 2. analyst
    if store.exists("03_analyst_artifact.json"):
        analyst = AnalystArtifact.model_validate_json(store.load_text("03_analyst_artifact.json"))
        store.mark_resumed("analyst")
    else:
        with store.step("analyst"):
            result = fns["analyst"](plan, evidence, store)
            analyst = result.artifact
            store.save_json("03_analyst_artifact.json", dump_contract(analyst))
            store.save_json("03_analyst_report.json", {
                "unsupported_numbers": result.unsupported_numbers,
                "theme_rq_repairs": result.theme_rq_repairs,
                "source_quality": result.source_quality,
                "dropped_ids": result.dropped_ids,
            })

    # 3. strategy
    if store.exists("04_strategy_artifact.json"):
        strategy = StrategyArtifact.model_validate_json(store.load_text("04_strategy_artifact.json"))
        store.mark_resumed("strategy")
    else:
        with store.step("strategy"):
            result = fns["strategy"](analyst, store)
            strategy = result.artifact
            store.save_json("04_strategy_artifact.json", dump_contract(strategy))
            store.save_json("04_strategy_report.json", result.report)

    return PipelineResult(store.run_id, store.dir, plan, evidence, analyst, strategy)


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
            artifact = AnalystArtifact.model_validate_json(raw)
        except ValidationError as exc:
            rejected.append(f"{path.name}: not a valid AnalystArtifact ({exc.error_count()} errors)")
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
            "theme_rq_repairs": repairs,
            "unsupported_numbers": unsupported_number_flags(artifact, evidence),
            "source_quality": source_quality(artifact, evidence),
        })
        store.mark_salvaged("analyst", salvaged_from=path.name, dropped_id_count=len(dropped))
        return store.path("03_analyst_artifact.json")
    raise RuntimeError("no saved Analyst draft passes the hard checks: " + " | ".join(rejected))
