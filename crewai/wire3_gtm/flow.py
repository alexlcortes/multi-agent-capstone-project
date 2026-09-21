"""The pipeline as a CrewAI Flow.

The guide asks for a Flow that "passes the shared artifacts between roles". Each role is one
step, chained with @start / @listen, and the Flow's typed state is where the shared artifacts
live between steps: the plan, the evidence set, the Analyst artifact and the Strategy artifact,
all Pydantic models. The step bodies are the functions in pipeline.py, which also save every
artifact to the RunStore, so the run can still be resumed from disk after a failure.

    brief -> research (Head Planner + Research) -> analysis -> strategy -> link check -> document
"""

from crewai.flow.flow import Flow, listen, start
from pydantic import BaseModel

from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.evidence import EvidenceRecord
from wire3_gtm.models import ResearchPlan
from wire3_gtm.strategy_models import StrategyArtifact


class PipelineState(BaseModel):
    """The shared artifacts, each one a validated contract, handed from role to role."""

    plan: ResearchPlan | None = None
    evidence: list[EvidenceRecord] = []
    analyst: AnalystArtifact | None = None
    strategy: StrategyArtifact | None = None
    links: dict | None = None
    document: dict | None = None


class Wire3Flow(Flow[PipelineState]):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx  # RunContext: brief, RunStore, RunMonitor, step functions

    @start()
    def research(self):
        from wire3_gtm.pipeline import step_research

        self.state.plan, self.state.evidence = step_research(self.ctx)

    @listen(research)
    def analysis(self):
        from wire3_gtm.pipeline import step_analyst

        self.state.analyst = step_analyst(self.ctx, self.state.plan, self.state.evidence)

    @listen(analysis)
    def strategy(self):
        from wire3_gtm.pipeline import step_strategy

        self.state.strategy = step_strategy(self.ctx, self.state.analyst)

    @listen(strategy)
    def link_check(self):
        from wire3_gtm.pipeline import step_links

        self.state.links = step_links(self.ctx, self.state.analyst, self.state.evidence)

    @listen(link_check)
    def document(self):
        from wire3_gtm.pipeline import step_docs

        self.state.document = step_docs(self.ctx)
