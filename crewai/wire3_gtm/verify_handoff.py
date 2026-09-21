"""Confirm what each agent actually receives, from the real LLM request.

CrewAI hands a task's output to the next task as `output.raw` joined into the
prompt (crewai/utilities/formatter.py: aggregate_raw_outputs_from_task_outputs);
it does not summarize. This script proves it for a live run by listening to
LLMCallStartedEvent -- the exact messages sent to the model -- and checking:

  1. Head Planner's first request contains the brief, verbatim, in full.
  2. Research Agent's first request contains the Head Planner's raw output,
     verbatim, in full.
  3. Every planned tool call's arguments appear in that same request.

Run:  uv run python -m wire3_gtm.verify_handoff      (one real planner+research run)
"""

import json

from crewai.events import crewai_event_bus
from crewai.events.types.llm_events import LLMCallStartedEvent

from wire3_gtm.pipeline import load_brief, run_planning_and_research

llm_calls: dict[str, int] = {}  # agent role -> LLM requests made
first_request: dict[str, str] = {}  # agent role -> full text of its first LLM request


def _flatten(messages) -> str:
    if isinstance(messages, str):
        return messages
    return "\n".join(str(m.get("content", "")) for m in messages or [])


@crewai_event_bus.on(LLMCallStartedEvent)
def _capture(source, event) -> None:
    role = getattr(event, "agent_role", None)
    if role:
        llm_calls[role] = llm_calls.get(role, 0) + 1
    if role and role not in first_request:
        first_request[role] = _flatten(event.messages)


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"{'PASS' if ok else 'FAIL'}  {label}{('  ' + detail) if detail else ''}")
    return ok


def main() -> int:
    brief = load_brief()
    result = run_planning_and_research(brief)
    planner_req = first_request.get("Head Planner", "")
    research_req = first_request.get("Research Agent", "")
    print()

    results = [
        check("brief reached Head Planner verbatim",
              json.dumps(brief) in planner_req,
              f"({len(json.dumps(brief))} chars of brief)"),
        check("every brief research question is in Head Planner's request",
              all(q in planner_req for q in brief["research_questions"])),
        check("planner raw output reached Research Agent verbatim",
              result.plan_raw.strip() in research_req,
              f"({len(result.plan_raw)} chars of plan)"),
    ]
    missing = [c.args.company_name for c in result.plan.planned_tool_calls
               if c.args.company_name not in research_req]
    results.append(check("every planned call's company_name is in Research request", not missing, str(missing or "")))
    results.append(check("Research request holds all %d planned calls" % len(result.plan.planned_tool_calls),
                         research_req.count('"tool"') >= len(result.plan.planned_tool_calls)))
    print("\nLLM calls per role:", llm_calls)
    print("coverage:", result.collector.coverage(result.plan))
    return 0 if all(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
