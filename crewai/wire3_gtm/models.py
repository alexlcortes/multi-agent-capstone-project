"""Pydantic contracts for task outputs.

ResearchPlan mirrors the n8n Head Planner's structured output. The Analyst and
Strategy artifacts still need to be translated from schemas/*.schema.json,
including the basis-conditional validators.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ResearchQuestion(BaseModel):
    id: str = Field(pattern=r"^RQ\d+$")
    question: str
    priority: Literal["high", "medium", "low"]


class ToolArgs(BaseModel):
    """Explicit rather than dict: OpenAI structured output rejects open objects.
    region is null for tools that take only company_name."""

    company_name: str
    region: str | None = None


class PlannedToolCall(BaseModel):
    tool: Literal[
        "company_overview",
        "competitor_discovery",
        "product_portfolio_mapping",
        "pricing_research",
        "recent_news",
    ]
    args: ToolArgs
    research_question_ids: list[str] = Field(min_length=1)


class Budget(BaseModel):
    max_search_calls: int
    max_wall_clock_minutes: int
    max_llm_calls_per_role: int
    max_cost_usd: float


class ResearchPlan(BaseModel):
    run_id: str
    region: str
    segment: str
    competitors: list[str]
    research_questions: list[ResearchQuestion]
    planned_tool_calls: list[PlannedToolCall]
    budget: Budget
    assumptions_and_gaps: list[str]

    @model_validator(mode="after")
    def plan_is_bounded_and_complete(self):
        if len(self.planned_tool_calls) > self.budget.max_search_calls:
            raise ValueError("planned_tool_calls exceeds budget.max_search_calls")
        served = {i for c in self.planned_tool_calls for i in c.research_question_ids}
        missing = {q.id for q in self.research_questions} - served
        if missing:
            raise ValueError(f"research questions not served by any call: {sorted(missing)}")
        unknown = served - {q.id for q in self.research_questions}
        if unknown:
            raise ValueError(f"calls reference unknown research questions: {sorted(unknown)}")
        return self


def plan_guardrail(output):
    """Task guardrail: on failure CrewAI re-prompts the Head Planner with the
    message. Every call must target a real company from the brief, not an
    invented placeholder (topic-level questions map onto the named companies)."""
    from wire3_gtm.wire_models import strict_or_feedback

    plan, feedback = strict_or_feedback(ResearchPlan, output)  # the bounded/complete rules run here, not in the SDK
    if plan is None:
        return False, feedback
    output.pydantic = plan
    allowed = {"wire3", *(c.strip().lower() for c in plan.competitors)}
    bad = sorted({c.args.company_name for c in plan.planned_tool_calls
                  if c.args.company_name.strip().lower() not in allowed})
    if bad:
        return False, (
            f"company_name must be Wire3 or one of {plan.competitors}; got {bad}. "
            "Map topic-level research questions onto those companies."
        )
    # Same rule as n8n's plan gate: the plan must research Wire3 itself. An n8n run planned only competitor
    # and topic calls, and its analysis had no Wire3 evidence at all.
    if not any(c.args.company_name.strip().lower() == "wire3" for c in plan.planned_tool_calls):
        return False, ("The plan has no Wire3 research calls. Research Wire3 itself too: add company_overview, "
                       "product_portfolio_mapping, pricing_research and recent_news calls with company_name Wire3.")
    return True, output
