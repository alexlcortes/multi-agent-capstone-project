"""MCP research tools for the Research Agent, with every call recorded."""

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

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


@dataclass
class RetryPolicy:
    """Bounded retries with exponential backoff (guide: 'bounded retries with backoff
    for transient failures'). 3 attempts, waiting 2s then 4s."""

    max_attempts: int = 3
    base_delay_s: float = 2.0
    sleep: Callable[[float], None] = time.sleep


# Failures worth retrying: the search provider or the network hiccuped. A bad argument
# ("company_name must not be empty") will fail the same way again, so it is not retried.
_TRANSIENT = ("search provider failed", "timed out", "timeout", "connection", "temporarily",
              "rate limit", "429", "502", "503", "504")


def is_transient(exc: BaseException) -> bool:
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    return any(marker in str(exc).lower() for marker in _TRANSIENT)


def _recording(inner: BaseTool, collector: EvidenceCollector, monitor=None,
               policy: RetryPolicy | None = None) -> BaseTool:
    """Wrap an MCP tool so its raw output goes to the collector before the agent sees it.

    * Transient failures are retried with backoff. Every attempt is counted against the
      search budget (a retry is a real provider call), and only the final outcome is recorded,
      so retrying never produces duplicate evidence.
    * A failed call is returned as text so one failure does not abort the run (the Research
      Agent's prompt says to continue), and it stays visible in the record with its attempt count.
    * Once the search budget is spent, calls are refused and recorded as budget_exceeded."""
    policy = policy or RetryPolicy()

    class RecordingTool(BaseTool):
        name: str = inner.name
        description: str = inner.description
        args_schema: type = inner.args_schema

        def _run(self, **kwargs) -> str:
            # The planner may attach a region to tools that only take company_name. Drop
            # arguments the tool does not declare (and nulls); what is recorded is what was sent.
            allowed = set(inner.args_schema.model_fields)
            kwargs = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
            t0, errors, attempts = time.time(), [], 0
            ms = lambda: int((time.time() - t0) * 1000)  # noqa: E731
            while attempts < policy.max_attempts:
                stop = monitor.stop_reason() if monitor is not None else None
                if stop:  # time or cost spent: refuse, and the step boundary after Research raises
                    collector.record(inner.name, kwargs, None, error=stop, attempts=attempts, duration_ms=ms(),
                                     attempt_errors=errors, status="budget_exceeded")
                    return f"TOOL ERROR: {stop}. Do not make further search calls."
                if monitor is not None and not monitor.take_search_slot():
                    msg = f"search budget exhausted ({monitor.budget.max_search_calls} calls)"
                    collector.record(inner.name, kwargs, None, error=msg, attempts=attempts, duration_ms=ms(),
                                     attempt_errors=errors, status="budget_exceeded")
                    return f"TOOL ERROR: {msg}. Do not make further search calls."
                attempts += 1
                try:
                    raw = inner.run(**kwargs)
                except Exception as exc:  # noqa: BLE001 -- record any tool failure
                    errors.append(f"{type(exc).__name__}: {exc}"[:300])
                    if attempts < policy.max_attempts and is_transient(exc):
                        policy.sleep(policy.base_delay_s * 2 ** (attempts - 1))
                        continue
                    collector.record(inner.name, kwargs, None, error=errors[-1], attempts=attempts,
                                     duration_ms=ms(), attempt_errors=errors)
                    return f"TOOL ERROR: {exc}"
                collector.record(inner.name, kwargs, raw, attempts=attempts, duration_ms=ms(), attempt_errors=errors)
                return raw
            raise AssertionError("unreachable")  # pragma: no cover

    return RecordingTool()


@contextmanager
def research_tools(collector: EvidenceCollector, url: str = MCP_URL, monitor=None,
                   policy: RetryPolicy | None = None) -> Iterator[list[BaseTool]]:
    """Connect to the MCP server and yield the five research tools, wrapped.
    The connection must stay open for the whole crew run."""
    params = {"url": url, "transport": "streamable-http"}
    with MCPServerAdapter(params) as tools:
        wanted = [t for t in tools if t.name in RESEARCH_TOOLS]
        missing = set(RESEARCH_TOOLS) - {t.name for t in wanted}
        if missing:
            raise RuntimeError(f"MCP server at {url} is missing tools: {sorted(missing)}")
        yield [_recording(t, collector, monitor, policy) for t in wanted]


def enforce_plan(plan, collector: EvidenceCollector, tools: list, monitor=None) -> list[str]:
    """Run, in code, every planned call the Research Agent skipped (a live run skipped 2 of 15 and the
    pipeline carried on). Each planned call is fully specified (tool + arguments), so nothing is left to
    interpret. Calls go through the same recording wrappers, so retries, backoff, the search budget and
    evidence recording all apply, and each one is marked source="enforced" so the record still shows the
    agent skipped it. Returns a description of each call it ran; idempotent."""
    from concurrent.futures import ThreadPoolExecutor

    missing = collector.missing_planned(plan)
    if not missing:
        return []
    by_name = {t.name: t for t in tools}

    def run_one(planned) -> None:
        args = {k: v for k, v in planned.args.model_dump().items() if v is not None}
        tool = by_name.get(planned.tool)
        try:
            if tool is None:
                raise KeyError(f"no research tool named {planned.tool!r}")
            tool.run(**args)  # records itself, with retries and the search budget
        except Exception as exc:  # noqa: BLE001 -- argument validation errors surface before the wrapper runs
            collector.record(planned.tool, args, None, error=f"{type(exc).__name__}: {exc}"[:300])

    collector.source = "enforced"
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(run_one, missing))
    finally:
        collector.source = "agent"
    described = [f"{p.tool}({p.args.company_name}{', ' + p.args.region if p.args.region else ''})" for p in missing]
    if monitor is not None:
        monitor.event("validation_gate", node="Research Agent", gate="plan_enforcement", status="degraded",
                      skipped_by_agent=described, ran_by_pipeline=len(described))
    return described


class McpUnavailable(RuntimeError):
    pass


def mcp_preflight(url: str = MCP_URL) -> dict:
    """Fail fast, with a clear message, if the MCP research server is unreachable, and report
    which search provider it will use (never key values). Answers the guide's question: are
    MCP and the selected search provider available to the intended agent?"""
    try:
        with MCPServerAdapter({"url": url, "transport": "streamable-http"}) as tools:
            names = {t.name for t in tools}
            missing = sorted(set(RESEARCH_TOOLS) - names)
            if missing:
                raise McpUnavailable(f"MCP server at {url} is missing tools: {missing}")
            health = next((t for t in tools if t.name == "health_check"), None)
            info = json.loads(health.run()) if health else {}
    except McpUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise McpUnavailable(
            f"cannot reach the MCP research server at {url} ({type(exc).__name__}: {exc}). "
            "Start it: cd mcp-server && uv run python main.py"
        ) from exc
    if info and not info.get(f"{info.get('active_provider')}_configured", True):
        raise McpUnavailable(f"MCP server is using search provider {info.get('active_provider')!r} but no API key is configured for it")
    return {"tools": sorted(RESEARCH_TOOLS), **info}
