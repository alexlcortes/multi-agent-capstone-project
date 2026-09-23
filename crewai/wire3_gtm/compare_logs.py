"""Side-by-side comparison of the latest completed n8n and CrewAI runs.

    uv run python -m wire3_gtm.compare_logs [--crewai-run RUN_ID] [--n8n-run CLIENT_RUN_ID]

Reads n8n/logs/runs.jsonl and crewai/logs/runs.jsonl. The two record some things differently, and
the table says so instead of hiding it: n8n token counts are character estimates without hidden
reasoning tokens (cost is a lower bound), n8n's retry figure is a count of HTTP 429s in a server
log, CrewAI's is a real count of attempts.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
N8N_LOG = ROOT / "n8n" / "logs" / "runs.jsonl"
CREWAI_LOG = ROOT / "crewai" / "logs" / "runs.jsonl"


def read_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def latest_run(events: list[dict], run_id: str | None = None) -> tuple[dict | None, dict | None]:
    """(run_complete, usage_summary) for the newest finished run, or the one named."""
    done = [e for e in events if e["event_type"] == "run_complete"
            and (run_id is None or e.get("client_run_id") == run_id)
            and e.get("status") in ("success", "degraded")]
    if not done:
        return None, None
    rc = done[-1]
    usage = next((e for e in reversed(events) if e["event_type"] == "usage_summary"
                  and e.get("client_run_id") == rc.get("client_run_id")), None)
    return rc, usage


def _agent_calls(usage: dict | None) -> str:
    if not usage:
        return "n/a"
    return ", ".join(f"{a['agent']} {a['llm_calls']}" for a in usage["per_agent"])


def rows(n8n: tuple, crew: tuple) -> list[tuple[str, str, str]]:
    (n_rc, n_us), (c_rc, c_us) = n8n, crew
    g = lambda d, k, f=lambda v: v: "n/a" if not d or d.get(k) is None else f(d[k])  # noqa: E731
    minutes = lambda v: f"{v / 60000:.1f} min"  # noqa: E731
    return [
        ("status", g(n_rc, "status"), g(c_rc, "status")),
        ("latency (brief -> done)", g(n_rc, "duration_ms", minutes), g(c_rc, "duration_ms", minutes)),
        ("within 12 min budget / <15 min KPI",
         "n/a" if not n_rc else f"{n_rc['duration_ms'] <= 12 * 60000} / {n_rc['duration_ms'] < 15 * 60000}",
         "n/a" if not c_rc else f"{c_rc['latency_within_budget']} / {c_rc['latency_within_kpi']}"),
        ("research questions answered", g(n_rc, "research_questions_answered", str) + "/" + g(n_rc, "research_questions_total", str),
         g(c_rc, "research_questions_answered", str) + "/" + g(c_rc, "research_questions_total", str)),
        ("evidence records", g(n_rc, "evidence_count", str), g(c_rc, "evidence_count", str)),
        ("tool calls executed / planned", g(n_rc, "tool_calls_executed", str) + "/" + g(n_rc, "tool_calls_planned", str),
         g(c_rc, "tool_calls_executed", str) + "/" + g(c_rc, "tool_calls_planned", str)),
        ("retries", f"{g(n_rc, 'rate_limit_errors_logged', str)} HTTP 429s in server log (approximate)",
         "n/a" if not c_rc else f"{c_rc['retries']['total']} counted ({c_rc['retries']})"),
        ("LLM calls by agent", _agent_calls(n_us), _agent_calls(c_us)),
        ("total tokens (in / out)", "n/a" if not n_us else f"{n_us['total_prompt_tokens']} / {n_us['total_completion_tokens']} (estimated)",
         "n/a" if not c_us else f"{c_us['total_prompt_tokens']} / {c_us['total_completion_tokens']} (provider-reported, {c_us['total_reasoning_tokens']} reasoning)"),
        ("LLM cost (USD)", "n/a" if not n_us else f"{n_us['estimated_llm_cost_usd']} (LOWER BOUND: no reasoning tokens)",
         "n/a" if not c_us else f"{c_us['estimated_llm_cost_usd']} (includes reasoning tokens)"),
        ("cost budget (USD)", g(n_rc, "budget_max_cost_usd", str), g(c_rc, "budget_max_cost_usd", str)),
        ("broken / invalid URLs", f"n/a / {g(n_rc, 'invalid_url_count', str)} (format check only)",
         f"{g(c_rc, 'broken_url_count', str)} / {g(c_rc, 'invalid_url_count', str)} (HEAD-checked)"),
        ("document", g(n_rc, "document_write_status"), g(c_rc, "document_write_status")),
        ("issues", "; ".join(n_rc.get("issues", [])) or "none" if n_rc else "n/a",
         "; ".join(c_rc.get("issues", [])) or "none" if c_rc else "n/a"),
    ]


def render(n8n: tuple, crew: tuple) -> str:
    table = rows(n8n, crew)
    w = max(len(r[0]) for r in table)
    out = [f"{'':{w}}  {'n8n':<52}  CrewAI", "-" * (w + 110)]
    for label, a, b in table:
        out.append(f"{label:{w}}  {a[:50]:<52}  {b}")
    return "\n".join(out)


def brief_warning(n8n_events: list[dict], crew_events: list[dict], n8n_rc: dict | None, crew_rc: dict | None) -> str | None:
    """The two runs must share a brief to be comparable; run_record.brief_id is its content hash."""
    def bid(events, rc):
        return next((e["brief_id"] for e in events if e["event_type"] == "run_record"
                     and rc and e["run_id"] == rc.get("client_run_id")), None)
    n, c = bid(n8n_events, n8n_rc), bid(crew_events, crew_rc)
    if n and c and n == c:
        return None
    if n is None or c is None:
        return (f"WARNING: cannot confirm the two runs share a brief (brief_id n8n={n}, crewai={c}); "
                "add run records with `node scripts/run_record.js` / `python -m wire3_gtm run-records`.")
    return (f"WARNING: these runs were given DIFFERENT briefs (n8n {n}, crewai {c}), so they are not comparable. "
            "Rerun n8n with `node scripts/run_pipeline.js` (posts the shared brief.json).")


def main(argv: list[str]) -> int:
    opt = lambda flag: argv[argv.index(flag) + 1] if flag in argv else None  # noqa: E731
    n8n_events, crew_events = read_events(N8N_LOG), read_events(CREWAI_LOG)
    n8n = latest_run(n8n_events, opt("--n8n-run"))
    crew = latest_run(crew_events, opt("--crewai-run"))
    if not n8n[0] or not crew[0]:
        print("Missing a finished run:", "n8n" if not n8n[0] else "", "crewai" if not crew[0] else "")
    print(render(n8n, crew))
    warning = brief_warning(n8n_events, crew_events, n8n[0], crew[0])
    if warning:
        print("\n" + warning)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
