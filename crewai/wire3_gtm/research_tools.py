"""MCP research tools for the Research Agent, with every call recorded."""

from contextlib import contextmanager
from typing import Iterator

from crewai.tools import BaseTool
from crewai_tools import MCPServerAdapter

from wire3_gtm.evidence import EvidenceCollector

MCP_URL = "http://127.0.0.1:8000/mcp"

# The only tools the Head Planner may plan. health_check and validate_source
# exist on the server but are not part of the Research Agent's job.
RESEARCH_TOOLS = (
    "company_overview",
    "competitor_discovery",
    "product_portfolio_mapping",
    "pricing_research",
    "recent_news",
)


def _recording(inner: BaseTool, collector: EvidenceCollector) -> BaseTool:
    """Wrap an MCP tool so its raw output goes to the collector before the
    agent sees it. Tool errors are recorded and returned as text so one failed
    call does not abort the run (the Research Agent's prompt says to continue)."""

    class RecordingTool(BaseTool):
        name: str = inner.name
        description: str = inner.description
        args_schema: type = inner.args_schema

        def _run(self, **kwargs) -> str:
            # The planner may attach a region to tools that only take
            # company_name. Drop arguments the tool does not declare (and nulls)
            # rather than failing the call; what is recorded is what was sent.
            allowed = set(inner.args_schema.model_fields)
            kwargs = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
            try:
                raw = inner.run(**kwargs)
            except Exception as exc:  # noqa: BLE001 -- record any tool failure
                collector.record(inner.name, kwargs, None, error=f"{type(exc).__name__}: {exc}")
                return f"TOOL ERROR: {exc}"
            collector.record(inner.name, kwargs, raw)
            return raw

    return RecordingTool()


@contextmanager
def research_tools(collector: EvidenceCollector, url: str = MCP_URL) -> Iterator[list[BaseTool]]:
    """Connect to the MCP server and yield the five research tools, wrapped.
    The connection must stay open for the whole crew run."""
    params = {"url": url, "transport": "streamable-http"}
    with MCPServerAdapter(params) as tools:
        wanted = [t for t in tools if t.name in RESEARCH_TOOLS]
        missing = set(RESEARCH_TOOLS) - {t.name for t in wanted}
        if missing:
            raise RuntimeError(f"MCP server at {url} is missing tools: {sorted(missing)}")
        yield [_recording(t, collector) for t in wanted]
