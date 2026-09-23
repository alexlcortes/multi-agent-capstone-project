"""One comparable run_record per run (schemas/run_record.schema.json).

Derived from the run's own events in logs/runs.jsonl, so it can be built at the
end of a run or afterwards for runs logged before it existed. n8n builds the
same record in n8n/scripts/run_record.js; the schema is the contract between them.
"""

import hashlib
import json
from datetime import datetime, timezone

AGENTS = ("Head Planner", "Research Agent", "Analyst Agent", "Strategy Agent", "Docs Writer")
DOC_STATUS = {"verified": "verified", "local_verified": "local_only", "not_run": "not_run", None: "not_run"}


def brief_id(brief: dict | None) -> str | None:
    """Same algorithm as n8n/scripts/run_record.js: sha256 over canonical JSON."""
    if brief is None:
        return None
    canon = json.dumps(brief, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "brief-" + hashlib.sha256(canon.encode()).hexdigest()[:12]


def _one(events, event_type, **match):
    found = [e for e in events if e["event_type"] == event_type and all(e.get(k) == v for k, v in match.items())]
    return found[-1] if found else None


def _sum(values):
    values = [v for v in values if v is not None]
    return sum(values) if values else None


def build(events: list[dict], brief: dict | None, now: datetime | None = None) -> dict:
    """events: every event of one run (same client_run_id), in log order."""
    rc = _one(events, "run_complete")
    if rc is None:
        raise ValueError("run has no run_complete event")
    us = _one(events, "usage_summary") or {}
    start = _one(events, "pipeline_start") or {}
    preflight = _one(events, "validation_gate", gate="mcp_preflight") or {}
    retries = rc.get("retries") or {}
    usage = {a["agent"]: a for a in us.get("per_agent", [])}

    agents = []
    for name in AGENTS:
        s, e = _one(events, "agent_start", agent=name), _one(events, "agent_end", agent=name)
        if s is None and e is None:
            continue
        u = usage.get(name, {})
        agents.append({
            "agent": name, "status": (e or {}).get("status", "started" if s else None),
            "started_at": (s or {}).get("ts"), "ended_at": (e or {}).get("ts"),
            "duration_ms": (e or {}).get("duration_ms"),
            "llm_calls": u.get("llm_calls", 0 if name == "Docs Writer" else None),
            "prompt_tokens": u.get("prompt_tokens", 0 if name == "Docs Writer" else None),
            "completion_tokens": u.get("completion_tokens", 0 if name == "Docs Writer" else None),
            "reasoning_tokens": u.get("reasoning_tokens", 0 if name == "Docs Writer" else None),
            "cost_usd": u.get("cost_usd", 0 if name == "Docs Writer" else None),
            "retries": (retries.get("guardrail_retries") or {}).get(name, 0),
            "error": (e or {}).get("error"),
        })

    by_tool: dict[str, dict] = {}
    for t in (e for e in events if e["event_type"] == "tool_call"):
        slot = by_tool.setdefault(t["tool"], {"calls": 0, "errors": 0})
        slot["calls"] += 1
        slot["errors"] += t.get("status") != "ok"

    errors = [{"event_type": e["event_type"], "node": e.get("node"), "message": str(e.get("error") or e.get("gate") or e["status"])[:500]}
              for e in events if e.get("status") in ("error", "failed") and e["event_type"] != "usage_summary"]
    links_ran = rc.get("broken_url_count") is not None
    doc = _one(events, "document_write") or {}
    write_status = DOC_STATUS.get(rc.get("document_write_status"), "failed")
    cost = rc.get("estimated_llm_cost_usd")
    duration = rc.get("duration_ms")
    max_min = rc.get("budget_max_wall_clock_minutes")
    search, max_search = rc.get("search_calls_attempted"), rc.get("budget_max_search_calls")

    return {
        "schema_version": 1, "event_type": "run_record",
        "ts": (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "implementation": "crewai", "run_id": rc["client_run_id"], "brief_id": brief_id(brief),
        "planner_run_id": rc.get("plan_run_id"),
        "started_at": start.get("ts"), "ended_at": rc["ts"], "duration_ms": duration,
        "status": rc["status"], "failed_step": rc.get("failed_step"), "errors": errors,
        "issues": rc.get("issues") or [],
        "model": rc.get("model"), "provider": rc.get("provider"), "search_provider": preflight.get("active_provider"),
        "agents": agents,
        "tools": {"planned": rc.get("tool_calls_planned"), "executed": rc.get("tool_calls_executed"),
                  "failed": rc.get("tool_calls_failed"), "retried": rc.get("tool_calls_retried"), "by_tool": by_tool},
        "retries": {"total": retries.get("total"), "tool_call": retries.get("tool_call_retries"),
                    "guardrail": _sum((retries.get("guardrail_retries") or {}).values()) or 0,
                    "provider": retries.get("provider_retries"), "basis": "counted"},
        "tokens": {"prompt": us.get("total_prompt_tokens"), "completion": us.get("total_completion_tokens"),
                   "reasoning": us.get("total_reasoning_tokens"),
                   "cached_prompt": _sum(a.get("cached_prompt_tokens") for a in usage.values()),
                   "source": "provider_reported" if us else "unavailable"},
        "cost": {"estimated_llm_usd": cost, "is_lower_bound": us.get("cost_is_lower_bound"),
                 "excludes": us.get("cost_excludes") or []},
        "research": {"questions_total": rc.get("research_questions_total"),
                     "questions_answered": rc.get("research_questions_answered")},
        "evidence": {"records": rc.get("evidence_count"), "coverage_percent": rc.get("evidence_coverage_percent")},
        "links": {"checked": rc.get("linked_sources_cited") if links_ran else None,
                  "broken": rc.get("broken_url_count"), "invalid": rc.get("invalid_url_count") if links_ran else None,
                  "blocked": rc.get("blocked_url_count"), "unverified": rc.get("unverified_url_count")},
        "document": {"write_status": write_status, "verified": write_status == "verified",
                     "url": rc.get("document_url"), "sections": doc.get("section_count"),
                     "sources_cited": doc.get("source_count")},
        "budget": {"max_cost_usd": rc.get("budget_max_cost_usd"), "max_search_calls": max_search,
                   "max_wall_clock_minutes": max_min, "search_calls": search,
                   "within_latency": duration <= max_min * 60_000 if duration is not None and max_min else None,
                   "within_cost": cost <= rc["budget_max_cost_usd"] if cost is not None and rc.get("budget_max_cost_usd") else None,
                   "within_search_calls": search <= max_search if search is not None and max_search else None},
        "not_measured": {},
    }


def events_by_run(log_text: str) -> dict[str, list[dict]]:
    runs: dict[str, list[dict]] = {}
    for line in log_text.splitlines():
        if line.strip():
            e = json.loads(line)
            key = e["run_id"] if e.get("event_type") == "run_record" else e.get("client_run_id")
            runs.setdefault(key, []).append(e)
    return runs


def backfill(log_path, brief_for) -> list[str]:
    """Append a run_record for every finished run in the log that has none yet.
    brief_for(client_run_id) returns the brief that run was given, or None."""
    from pathlib import Path

    log_path = Path(log_path)
    added = []
    with log_path.open("a") as out:
        for cid, events in events_by_run(log_path.read_text()).items():
            types = {e["event_type"] for e in events}
            if "run_complete" in types and "run_record" not in types:
                out.write(json.dumps(build(events, brief_for(cid))) + "\n")
                added.append(cid)
    return added
