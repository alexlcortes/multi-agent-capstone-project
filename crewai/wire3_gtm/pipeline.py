"""Planning + research phase: brief -> Head Planner -> Research Agent -> evidence set."""

import json
from dataclasses import dataclass
from pathlib import Path

from crewai import Crew, Process

from wire3_gtm.agents import build_agents
from wire3_gtm.analyst_checks import (
    coverage_errors, make_analyst_guardrail, repair_theme_rqs, source_quality, theme_rq_errors,
    unsupported_number_flags,
)
from wire3_gtm.analyst_models import AnalystArtifact, check_grounding
from wire3_gtm.evidence import EvidenceCollector, EvidenceRecord, EvidenceSet
from wire3_gtm.models import ResearchPlan
from wire3_gtm.research_tools import research_tools
from wire3_gtm.tasks import build_tasks

BRIEF_PATH = Path(__file__).parent.parent / "brief.json"


@dataclass
class PhaseResult:
    plan: ResearchPlan
    plan_raw: str  # exactly what CrewAI passes to the next task as context
    evidence: list[EvidenceRecord]
    collector: EvidenceCollector


def run_planning_and_research(brief: dict) -> PhaseResult:
    collector = EvidenceCollector()
    with research_tools(collector) as tools:
        agents = build_agents(research_tools=tools)
        tasks = build_tasks(agents)
        phase = {k: tasks[k] for k in ("plan_research", "execute_research")}
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


def run_analyst(plan: ResearchPlan, evidence: list[EvidenceRecord]) -> AnalystResult:
    """Analyst step. The evidence goes in as an EvidenceSet JSON document and
    comes out as a validated AnalystArtifact; no free text in between."""
    evidence_set = EvidenceSet(run_id=plan.run_id, evidence=evidence)
    agents = build_agents()
    tasks = build_tasks(agents, guardrails={"analyze_evidence": make_analyst_guardrail(evidence)})
    task = tasks["analyze_evidence"]
    Crew(agents=[agents["analyst"]], tasks=[task], process=Process.sequential).kickoff(
        inputs={"evidence_set": evidence_set.model_dump_json()}
    )
    artifact = task.output.pydantic
    if artifact is None:
        raise RuntimeError("Analyst did not return a valid AnalystArtifact")
    # Belt and braces: the guardrail retries a bounded number of times, so
    # re-run the hard checks on whatever came back.
    theme_rq_repairs = repair_theme_rqs(artifact, evidence)
    valid_ids = {e.evidence_id for e in evidence}
    errors = check_grounding(artifact, valid_ids) + theme_rq_errors(artifact, evidence) + coverage_errors(artifact, evidence)
    if errors:
        raise RuntimeError(f"Analyst artifact failed hard checks after retries: {errors[:5]}")
    return AnalystResult(
        artifact=artifact,
        unsupported_numbers=unsupported_number_flags(artifact, evidence),
        theme_rq_repairs=theme_rq_repairs,
        source_quality=source_quality(artifact, evidence),
    )
