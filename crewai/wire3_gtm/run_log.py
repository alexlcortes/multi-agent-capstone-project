"""Run logging, retry/cost/latency tracking and budget enforcement.

Writes the same one-JSON-object-per-line event schema as n8n/logs/runs.jsonl
(pipeline_start, agent_start, agent_end, tool_call, validation_gate,
document_write, run_complete, usage_summary) with implementation="crewai", so
the two implementations can be compared field for field.

Where CrewAI does better than n8n, and where it does not:
  * token usage is PROVIDER-REPORTED per LLM call (n8n only had a character
    estimate), and includes hidden reasoning tokens, so the LLM cost here is
    exact for the model priced below, not a lower bound;
  * search-provider fees are still not included in cost;
  * a retry count is real: tool-call attempts, guardrail re-prompts and the
    OpenAI SDK's own transient retries are each counted, not inferred from a
    server log.
"""

import json
import logging
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from wire3_gtm.agents import model as current_model

LOG_PATH = Path(__file__).parent.parent / "logs" / "runs.jsonl"
PROVIDER = "openai"

# USD per 1M tokens. Copied from n8n/scripts/run_usage.js, where it was verified
# against OpenAI's pricing page on 2026-09-19. NOT re-verified here: update the
# value and the date together when prices change.
PRICING = {"gpt-5-mini": {"input": 0.25, "cached_input": 0.025, "output": 2.0, "verified": "2026-09-19"}}



def price_for(model: str) -> dict:
    """Prices for `model`: an A/B variant's own pricing_usd_per_1m wins, then PRICING. An unpriced model
    raises instead of reporting $0, because the cost budget and every A/B cost figure depend on it."""
    from wire3_gtm import variant

    p = variant.pricing() if variant.active() and variant.active().get("model") == model else None
    if p is None:
        p = PRICING.get(model)
    if p is None:
        raise KeyError(f"no price for model {model!r}: add pricing_usd_per_1m to the variant (or to run_log.PRICING)")
    return p


COST_EXCLUDES = ["search-provider fees (Tavily/SerpAPI)", "Google API usage (free at this volume)"]


class BudgetExceeded(Exception):
    """A budget limit was reached; the run stops with its artifacts kept."""


@dataclass
class Budget:
    """From the brief's 'Time and API budget' section."""

    max_search_calls: int = 40
    max_wall_clock_minutes: float = 12
    max_llm_calls_per_role: int = 6  # informational: see SETUP_DECISIONS (Research is exempt)
    max_cost_usd: float = 2.5
    max_completion_tokens_per_call: int = 32_000  # hard cap on one LLM reply (agents.py)
    # Time to keep in hand after the Analyst for Strategy + link check + document. Measured:
    # Strategy 68-82 s, links ~10 s, docs ~9 s across recorded runs; rounded up.
    reserve_after_analyst_s: float = 100

    def override(self, pairs: list[str]) -> dict[str, float]:
        """Apply `name=value` overrides (from `run --budget`); returns what changed."""
        changed = {}
        for pair in pairs:
            name, _, value = pair.partition("=")
            if name not in self.__dataclass_fields__:
                raise ValueError(f"unknown budget field {name!r}; one of {sorted(self.__dataclass_fields__)}")
            cast = type(getattr(self, name))
            setattr(self, name, cast(float(value)) if cast is int else cast(value))
            changed[name] = getattr(self, name)
        return changed

    @classmethod
    def from_brief(cls, brief: dict) -> "Budget":
        b = (brief or {}).get("budget", {})
        return cls(**{k: b[k] for k in cls.__dataclass_fields__ if k in b})


@dataclass
class Usage:
    llm_calls: int = 0
    failed_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0  # includes reasoning tokens, as OpenAI reports it
    cached_prompt_tokens: int = 0
    reasoning_tokens: int = 0

    def add(self, u: dict | None) -> None:
        self.llm_calls += 1
        u = u or {}
        self.prompt_tokens += int(u.get("prompt_tokens") or 0)
        self.completion_tokens += int(u.get("completion_tokens") or 0)
        self.cached_prompt_tokens += int(u.get("cached_prompt_tokens") or 0)
        self.reasoning_tokens += int(u.get("reasoning_tokens") or 0)

    def cost_usd(self, model: str | None = None) -> float:
        p = price_for(model or current_model())
        fresh = max(self.prompt_tokens - self.cached_prompt_tokens, 0)
        return (fresh * p["input"] + self.cached_prompt_tokens * p["cached_input"]
                + self.completion_tokens * p["output"]) / 1_000_000

    def snapshot(self) -> "Usage":
        return Usage(**self.__dict__)

    def minus(self, other: "Usage") -> "Usage":
        return Usage(**{k: getattr(self, k) - getattr(other, k) for k in self.__dict__})

    def as_fields(self) -> dict:
        return {"llm_calls": self.llm_calls, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "cached_prompt_tokens": self.cached_prompt_tokens,
                "reasoning_tokens": self.reasoning_tokens, "cost_usd": round(self.cost_usd(), 5)}


def _iso(ts: datetime | None = None) -> str:
    ts = (ts or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"


class RunMonitor:
    """Everything observable about one run. One instance per pipeline run."""

    def __init__(self, store, budget: Budget | None = None, log_path: Path | None = None, clock=time.time):
        # resolved at call time (not as a default argument) so tests can redirect LOG_PATH
        self.store, self.budget, self.clock = store, budget or Budget(), clock
        self.log_path = Path(log_path or LOG_PATH)
        self.t0 = clock()
        self.run_id: str | None = None  # the business run id, known once the plan exists
        self.lock = threading.Lock()
        self.usage: dict[str, Usage] = {}
        self.search_calls = 0
        self.tool_attempts = 0
        self.tool_retries = 0
        self.provider_retries = 0
        self.guardrail_rejections: Counter = Counter()
        self.active_role: str | None = None
        self._mark: dict[str, float] = {}
        self.last_attempt_s: dict[str, float] = {}
        self._starts: dict[str, tuple[datetime, Usage]] = {}
        self.role_results: dict[str, dict] = {}
        self.budget_overrides: dict[str, float] = {}  # set by `run --budget k=v` (test runs)

    # --- output -----------------------------------------------------------
    def event(self, event_type: str, **fields) -> dict:
        from wire3_gtm.brief_context import active

        rec = {"ts": _iso(fields.pop("_ts", None)), "implementation": "crewai", "event_type": event_type,
               "client_run_id": self.store.run_id, "run_id": self.run_id or self.store.run_id,
               "brief_key": active().key, **fields}
        with self.lock:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        return rec

    def run_record(self, brief: dict | None) -> dict:
        """The run's comparable summary (schemas/run_record.schema.json), built
        from the events this run wrote; call after run_complete + usage_summary."""
        from wire3_gtm.run_record import build, events_by_run

        with self.lock:
            events = events_by_run(self.log_path.read_text()).get(self.store.run_id, [])
            rec = build(events, brief)
            with self.log_path.open("a") as f:
                f.write(json.dumps(rec, default=str) + "\n")
        return rec

    # --- clock and budget -------------------------------------------------
    def elapsed_s(self) -> float:
        return self.clock() - self.t0

    def cost_usd(self) -> float:
        with self.lock:
            return sum(u.cost_usd() for u in self.usage.values())

    def stop_reason(self) -> str | None:
        """Why the run must stop now (time or cost limit reached), or None."""
        if self.elapsed_s() > self.budget.max_wall_clock_minutes * 60:
            return (f"wall-clock budget reached: {self.elapsed_s() / 60:.1f} min elapsed, "
                    f"limit {self.budget.max_wall_clock_minutes} min")
        if self.cost_usd() > self.budget.max_cost_usd:
            return f"cost budget reached: ${self.cost_usd():.3f} spent, limit ${self.budget.max_cost_usd}"
        return None

    def check_budget(self, where: str) -> None:
        """Stop, clearly, if a budget limit is reached. Called between steps, after every
        guardrail rejection, and (through the search wrapper) before every search call."""
        reason = self.stop_reason()
        if reason:
            head, tail = reason.split(":", 1)
            raise BudgetExceeded(f"{head} {where}:{tail}")

    def attempt_seconds(self, role: str) -> float:
        """Seconds since this role's previous attempt ended (or its task started), i.e. how long the
        attempt that just finished took. Called once per guardrail attempt."""
        now = self.clock()
        took = now - self._mark.get(role, now)
        self._mark[role] = now
        self.last_attempt_s[role] = took
        return took

    def can_afford_retry(self, role: str) -> bool:
        """Is there time for another attempt like the last one, plus the steps after this one?"""
        need = self.last_attempt_s.get(role, 0.0) + self.budget.reserve_after_analyst_s
        return self.elapsed_s() + need <= self.budget.max_wall_clock_minutes * 60

    def take_search_slot(self) -> bool:
        """Reserve one search call (each attempt counts, retries included). False when exhausted."""
        with self.lock:
            if self.search_calls >= self.budget.max_search_calls:
                return False
            self.search_calls += 1
            return True

    # --- LLM usage and retries (fed by CrewAI events / SDK logging) --------
    def on_llm_completed(self, role: str | None, usage: dict | None) -> None:
        with self.lock:
            self.usage.setdefault(role or "unknown", Usage()).add(usage)

    def on_llm_failed(self, role: str | None) -> None:
        with self.lock:
            self.usage.setdefault(role or "unknown", Usage()).failed_calls += 1

    def on_provider_retry(self) -> None:
        with self.lock:
            self.provider_retries += 1

    def on_task_started(self, role: str, ts: datetime | None = None) -> None:
        with self.lock:
            self.active_role = role
            self._mark[role] = self.clock()
            self._starts[role] = (ts or datetime.now(timezone.utc), self.usage.get(role, Usage()).snapshot())
        self.event("agent_start", node=role, agent=role, model=current_model(), provider=PROVIDER, status="ok", _ts=ts)

    def on_task_ended(self, role: str, status: str, ts: datetime | None = None, error: str | None = None) -> None:
        ts = ts or datetime.now(timezone.utc)
        with self.lock:
            start_ts, base = self._starts.get(role, (ts, Usage()))
            used = self.usage.get(role, Usage()).minus(base)
            failed = self.usage.get(role, Usage()).failed_calls
        rejections = self.guardrail_rejections.get(role, 0)
        self.role_results[role] = {"status": status, "duration_ms": int((ts - start_ts).total_seconds() * 1000)}
        self.event("agent_end", node=role, agent=role, model=current_model(), provider=PROVIDER, status=status,
                   duration_ms=self.role_results[role]["duration_ms"], **used.as_fields(),
                   failed_llm_calls=failed, guardrail_retries=rejections, error=(error or None), _ts=ts)

    # --- guardrail (quality) retries --------------------------------------
    def note_guardrail(self, role: str, gate: str, attempt: int, accepted: bool, feedback: str | None) -> None:
        if not accepted:
            with self.lock:
                self.guardrail_rejections[role] += 1
        self.event("validation_gate", node=role, gate=gate, attempt=attempt,
                   status="ok" if accepted else "rejected",
                   error=None if accepted else (feedback or "")[:500])

    # --- summaries --------------------------------------------------------
    def retries(self) -> dict:
        return {"tool_call_retries": self.tool_retries, "guardrail_retries": dict(self.guardrail_rejections),
                "provider_retries": self.provider_retries,
                "total": self.tool_retries + sum(self.guardrail_rejections.values()) + self.provider_retries}

    def usage_summary(self, status: str) -> dict:
        with self.lock:
            per_agent = [{"agent": r, "model": current_model(), **u.as_fields(), "failed_llm_calls": u.failed_calls,
                          "tokens_source": "provider_reported"} for r, u in self.usage.items()]
            total = Usage()
            for u in self.usage.values():
                for k in total.__dict__:
                    setattr(total, k, getattr(total, k) + getattr(u, k))
        m = current_model()
        p = price_for(m)
        return self.event(
            "usage_summary", node="RunMonitor", status=status, per_agent=per_agent,
            total_prompt_tokens=total.prompt_tokens, total_completion_tokens=total.completion_tokens,
            total_reasoning_tokens=total.reasoning_tokens, estimated_llm_cost_usd=round(total.cost_usd(), 5),
            cost_is_lower_bound=False, retries=self.retries(), search_tool_calls_attempted=self.search_calls,
            cost_basis={"pricing_usd_per_1m_tokens": {m: p}, "token_source": "provider-reported usage per LLM call"},
            cost_excludes=COST_EXCLUDES,
        )


# --- process-wide listeners, installed once, routed to the active monitor -------

_ACTIVE: RunMonitor | None = None
_installed = False


def activate(monitor: RunMonitor | None) -> None:
    global _ACTIVE
    _ACTIVE = monitor


class _SdkRetryHandler(logging.Handler):
    """The OpenAI SDK retries transient failures itself (429, 5xx, timeouts) and only
    says so in an INFO log line. Counting those lines makes those retries visible."""

    def emit(self, record: logging.LogRecord) -> None:
        if _ACTIVE is not None and "Retrying request" in record.getMessage():
            _ACTIVE.on_provider_retry()


def install_listeners() -> None:
    global _installed
    if _installed:
        return
    from crewai.events import crewai_event_bus
    from crewai.events.types.llm_events import LLMCallCompletedEvent, LLMCallFailedEvent
    from crewai.events.types.task_events import TaskCompletedEvent, TaskFailedEvent, TaskStartedEvent

    def role_of(event) -> str | None:
        task = getattr(event, "task", None)
        agent = getattr(task, "agent", None)
        return getattr(agent, "role", None) or getattr(event, "agent_role", None)

    @crewai_event_bus.on(LLMCallCompletedEvent)
    def _llm_done(source, event):
        if _ACTIVE:
            _ACTIVE.on_llm_completed(event.agent_role, event.usage)

    @crewai_event_bus.on(LLMCallFailedEvent)
    def _llm_failed(source, event):
        if _ACTIVE:
            _ACTIVE.on_llm_failed(event.agent_role)

    @crewai_event_bus.on(TaskStartedEvent)
    def _task_start(source, event):
        if _ACTIVE and role_of(event):
            _ACTIVE.on_task_started(role_of(event), event.timestamp)

    @crewai_event_bus.on(TaskCompletedEvent)
    def _task_done(source, event):
        if _ACTIVE and role_of(event):
            _ACTIVE.on_task_ended(role_of(event), "ok", event.timestamp)

    @crewai_event_bus.on(TaskFailedEvent)
    def _task_failed(source, event):
        if _ACTIVE and role_of(event):
            _ACTIVE.on_task_ended(role_of(event), "error", event.timestamp, error=str(event.error)[:500])

    sdk = logging.getLogger("openai._base_client")
    sdk.setLevel(min(sdk.level or logging.INFO, logging.INFO))
    sdk.addHandler(_SdkRetryHandler())
    _installed = True


def flush_events(timeout: float = 10.0) -> None:
    """CrewAI runs event handlers on a thread pool; wait so every log line is written."""
    from crewai.events import crewai_event_bus

    crewai_event_bus.flush(timeout=timeout)
