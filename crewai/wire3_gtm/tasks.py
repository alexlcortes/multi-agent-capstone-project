"""One task per agent, wired in pipeline order via `context`."""

import yaml
from crewai import Agent, Task

from wire3_gtm.agents import CONFIG_DIR
from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.strategy_models import StrategyArtifact
from wire3_gtm.models import ResearchPlan, plan_guardrail
from wire3_gtm.wire_models import wire_model

# Tasks with a Pydantic contract defined so far; the rest are validated downstream.
# The LLM call is given validator-free "wire" twins; the strict models validate in the guardrails
# (wire_models.py explains why: custom validators used to raise inside the OpenAI SDK).
OUTPUT_MODELS = {"plan_research": wire_model(ResearchPlan), "analyze_evidence": wire_model(AnalystArtifact),
                "build_strategy": wire_model(StrategyArtifact)}
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
