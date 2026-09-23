"""The four agent roles for the Wire3 GTM CrewAI pipeline."""

from pathlib import Path

import yaml
from crewai import LLM, Agent

CONFIG_DIR = Path(__file__).parent / "config"

# Same model as the n8n implementation, so the comparison isolates the framework.
# An A/B variant (wire3_gtm/variant.py) may override it for one run; read it through model().
DEFAULT_MODEL = MODEL = "gpt-5-mini"


def model() -> str:
    from wire3_gtm import variant

    return variant.model(DEFAULT_MODEL)

# Brief budget: max 6 LLM calls per agent role. max_iter bounds an agent's
# reasoning/tool loop. Research is the exception: it makes one LLM step per
# tool call, so a cap of 6 stopped it after ~6 of 15 planned calls. Its real
# bound is the plan (<= max_search_calls); 50 matches n8n's Research node. The
# 6-call budget is checked from counted LLM calls (verify_handoff), not max_iter.
MAX_ITER = {"head_planner": 6, "research": 50, "analyst": 6, "strategy": 6}

ROLE_KEYS = ("head_planner", "research", "analyst", "strategy")

# Hard cap on one LLM reply (reasoning tokens included, as OpenAI counts them). The largest measured
# reply was the Analyst's, ~15k completion tokens; 32k leaves room while bounding what a single runaway
# call can cost (32k x $2/1M = $0.064 of output). Overridable from the brief's budget.
DEFAULT_MAX_COMPLETION_TOKENS = 32_000


def _load_config() -> dict:
    from wire3_gtm import variant

    return variant.apply_agents(yaml.safe_load((CONFIG_DIR / "agents.yaml").read_text()))


def build_agents(research_tools: list | None = None,
                 max_completion_tokens: int | None = None) -> dict[str, Agent]:
    """Return the four agents keyed by role. Only Research gets tools."""
    config = _load_config()
    llm = LLM(model=model(), max_completion_tokens=max_completion_tokens or DEFAULT_MAX_COMPLETION_TOKENS)
    agents = {}
    for key in ROLE_KEYS:
        agents[key] = Agent(
            **config[key],
            llm=llm,
            tools=(research_tools or []) if key == "research" else [],
            allow_delegation=False,
            max_iter=MAX_ITER[key],
            verbose=True,
        )
    return agents
