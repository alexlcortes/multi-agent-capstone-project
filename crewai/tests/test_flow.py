import pytest
from crewai.flow.flow import Flow
from test_analyst_models import valid  # noqa: F401
from test_run_store import make_steps
from test_strategy import strategy  # noqa: F401

from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.flow import PipelineState, Wire3Flow
from wire3_gtm.models import ResearchPlan
from wire3_gtm.pipeline import RunContext
from wire3_gtm.run_log import RunMonitor
from wire3_gtm.run_store import RunStore
from wire3_gtm.strategy_models import StrategyArtifact


def flow_for(tmp_path, steps, docs=None):
    store = RunStore("r1", root=tmp_path)
    monitor = RunMonitor(store, log_path=tmp_path / "runs.jsonl")
    return Wire3Flow(RunContext({"b": 1}, store, monitor, steps, docs))


def test_the_pipeline_is_a_crewai_flow_over_typed_shared_artifacts(tmp_path, valid, strategy):
    flow = flow_for(tmp_path, make_steps(valid, strategy))
    assert isinstance(flow, Flow)
    flow.kickoff()
    s = flow.state
    assert isinstance(s, PipelineState)
    assert isinstance(s.plan, ResearchPlan) and len(s.evidence) == 1
    assert isinstance(s.analyst, AnalystArtifact) and isinstance(s.strategy, StrategyArtifact)
    assert s.links["broken_url_count"] == 0


def test_roles_run_in_pipeline_order(tmp_path, valid, strategy):
    order = []
    steps = make_steps(valid, strategy)
    for name in ("research", "analyst", "strategy", "links"):
        inner = steps[name].fn if hasattr(steps[name], "fn") else steps[name]
        steps[name] = (lambda n, f: (lambda *a: (order.append(n), f(*a))[1]))(name, inner)
    flow_for(tmp_path, steps).kickoff()
    assert order == ["research", "analyst", "strategy", "links"]


def test_a_failing_role_stops_the_chain_and_state_keeps_the_earlier_artifacts(tmp_path, valid, strategy):
    flow = flow_for(tmp_path, make_steps(valid, strategy, strategy_fails=True))
    with pytest.raises(RuntimeError, match="guardrail exhausted"):  # the original exception type, not a wrapper
        flow.kickoff()
    s = flow.state
    assert s.plan is not None and s.analyst is not None  # the research and analysis work is still in state
    assert s.strategy is None and s.links is None and s.document is None  # nothing after the failure ran
    assert (tmp_path / "r1" / "03_analyst_artifact.json").exists()  # and on disk, for resume
