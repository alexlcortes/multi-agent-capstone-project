"""One task per agent, wired in pipeline order via `context`."""

import yaml
from crewai import Agent, Task

from wire3_gtm.agents import CONFIG_DIR
from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.strategy_models import StrategyArtifact
from wire3_gtm.models import ResearchPlan, plan_guardrail

# Tasks with a Pydantic contract defined so far; the rest are validated downstream.
OUTPUT_MODELS = {"plan_research": ResearchPlan, "analyze_evidence": AnalystArtifact,
                "build_strategy": StrategyArtifact}
GUARDRAILS = {"plan_research": plan_guardrail}


def build_tasks(agents: dict[str, Agent], guardrails: dict | None = None) -> dict[str, Task]:
    """guardrails: per-run guardrails (e.g. the Analyst's, which needs the evidence ids)."""
    config = yaml.safe_load((CONFIG_DIR / "tasks.yaml").read_text())
    tasks: dict[str, Task] = {}
    for name, spec in config.items():  # YAML order == dependency order
        spec = dict(spec)
        agent = agents[spec.pop("agent")]
        context = [tasks[c] for c in spec.pop("context", [])]
        tasks[name] = Task(
            **spec,
            agent=agent,
            context=context,
            output_pydantic=OUTPUT_MODELS.get(name),
            guardrail=(guardrails or {}).get(name, GUARDRAILS.get(name)),
        )
    return tasks
