"""Evidence layer: EvidenceRecord contract plus recording of raw MCP tool output.

The Research Agent only *calls* tools. The evidence set is built here, in code,
from what the tools actually returned (same approach as n8n's "Build Evidence
Records" node), so no LLM ever paraphrases or invents a source.
"""

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from wire3_gtm.models import ResearchPlan


class EvidenceRecord(BaseModel):
    """Mirrors schemas/research_evidence.schema.json."""

    evidence_id: str = Field(pattern=r"^EV-[a-f0-9]{8}$")
    research_question_id: str | None = Field(default=None, pattern=r"^RQ[0-9]+$")
    claim: str = Field(min_length=1, max_length=400)
    source_title: str = Field(min_length=1)
    source_url: str = Field(pattern=r"^https?://")
    source_type: Literal["primary", "company", "analyst", "news", "community"]
    publication_date: str | None = None
    retrieval_timestamp: str
    excerpt: str = Field(min_length=1, max_length=600)
    validation_status: Literal[
        "verified", "unverified", "broken_link", "low_confidence", "conflicting"
    ] = "unverified"


class EvidenceSet(BaseModel):
    """The structured hand-off from Research to Analyst: { run_id, evidence[] },
    the input shape the n8n Analyst step also receives."""

    run_id: str
    evidence: list[EvidenceRecord]


def classify_source_type(url: str) -> str:
    u = (url or "").lower()
    if ".gov" in u:
        return "primary"
    if re.search(r"reddit\.com|trustpilot|bbb\.org", u):
        return "community"
    if re.search(r"fiercetelecom|lightreading|star-banner|starbanner", u):
        return "news"
    if re.search(r"spectrum\.com|att\.com|t-mobile\.com|wire3\.com", u):
        return "company"
    if re.search(r"broadbandnow|jdpower|j\.d\.\s?power|acsi|parksassociates", u):
        return "analyst"
    return "community"


def make_evidence_id(source_url: str, claim: str) -> str:
    """FNV-1a 32-bit over 'url|claim', identical to the n8n implementation so
    the same source yields the same id in both pipelines."""
    h = 0x811C9DC5
    for ch in f"{source_url}|{claim}":
        h ^= ord(ch)
        h = (h * 0x01000193) & 0xFFFFFFFF
    return f"EV-{h:08x}"


def parse_tool_output(raw: str) -> list[dict]:
    """Turn the MCP adapter's text result into a list of dicts.

    Observed shapes: one JSON object; a JSON array; or, for multi-item results,
    the str() of a Python list of JSON strings (['{...}', '{...}']).
    Raises json.JSONDecodeError if none of these fit."""
    raw = raw.strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        try:
            value = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            value = json.loads(raw)  # re-raise the JSON error
    items = value if isinstance(value, list) else [value]
    out = []
    for item in items:
        if isinstance(item, str):
            item = json.loads(item)
        if isinstance(item, dict):
            out.append(item)
    return out


@dataclass
class ToolCallRecord:
    tool: str
    args: dict
    status: Literal["ok", "empty_result", "failed", "budget_exceeded"]
    results: list[dict] = field(default_factory=list)
    error: str | None = None
    attempts: int = 1  # 1 = no retry. Only the FINAL outcome becomes evidence, so a retry never duplicates it.
    duration_ms: int | None = None
    attempt_errors: list[str] = field(default_factory=list)  # why each failed attempt failed


@dataclass
class EvidenceCollector:
    """Receives every tool call the Research Agent makes."""

    calls: list[ToolCallRecord] = field(default_factory=list)
    # If set, every call is appended here as one JSON line the moment it returns,
    # so paid search results survive a crash before the evidence set is built.
    sink: Path | None = None

    def _add(self, rec: ToolCallRecord) -> None:
        self.calls.append(rec)
        if self.sink is not None:
            with self.sink.open("a") as f:
                f.write(json.dumps(asdict(rec), default=str) + "\n")

    @classmethod
    def from_jsonl(cls, path: Path) -> "EvidenceCollector":
        """Rebuild a collector from a saved tool-call log (salvage after a crash)."""
        collector = cls()
        for line in path.read_text().splitlines():
            if line.strip():
                collector.calls.append(ToolCallRecord(**json.loads(line)))
        return collector

    def record(self, tool: str, args: dict, raw: str | None, error: str | None = None, *,
               attempts: int = 1, duration_ms: int | None = None, attempt_errors: list[str] | None = None,
               status: str | None = None) -> None:
        meta = {"attempts": attempts, "duration_ms": duration_ms, "attempt_errors": list(attempt_errors or [])}
        if error is not None:
            self._add(ToolCallRecord(tool, args, status or "failed", error=error, **meta))
            return
        try:
            results = parse_tool_output(raw or "")
        except json.JSONDecodeError as exc:
            self._add(ToolCallRecord(tool, args, "failed", error=f"unparseable output: {exc}", **meta))
            return
        found = [r for r in results if "source_url" in r]
        self._add(ToolCallRecord(tool, args, "ok" if found else "empty_result", found, **meta))

    def match_plan(self, plan: ResearchPlan) -> list[list[str]]:
        """For each recorded call, the RQ ids of the planned call it fulfils
        (same tool, same args; each planned call is consumed once)."""
        unused = list(plan.planned_tool_calls)
        matched: list[list[str]] = []
        for call in self.calls:
            hit = next(
                (p for p in unused if p.tool == call.tool and _same_args(p.args.model_dump(), call.args)), None
            )
            if hit is None:  # fall back to same tool, like the n8n matcher
                hit = next((p for p in unused if p.tool == call.tool), None)
            if hit is not None:
                unused.remove(hit)
            matched.append(hit.research_question_ids if hit else [])
        return matched

    def build_evidence(self, plan: ResearchPlan) -> list[EvidenceRecord]:
        evidence: dict[str, EvidenceRecord] = {}
        for call, rq_ids in zip(self.calls, self.match_plan(plan)):
            for r in call.results:
                text = str(r.get("excerpt") or "")
                claim = text[:400] or "No excerpt available"
                for rq in rq_ids or [None]:
                    rec = EvidenceRecord(
                        evidence_id=make_evidence_id(r["source_url"], f"{claim}|{rq or ''}"),
                        research_question_id=rq,
                        claim=claim,
                        source_title=r.get("source_title") or "Untitled source",
                        source_url=r["source_url"],
                        source_type=classify_source_type(r["source_url"]),
                        publication_date=r.get("publication_date"),
                        retrieval_timestamp=r.get("retrieval_timestamp", ""),
                        excerpt=text[:600] or "No excerpt available",
                    )
                    evidence.setdefault(rec.evidence_id, rec)
        return list(evidence.values())

    def coverage(self, plan: ResearchPlan) -> dict:
        planned, made = len(plan.planned_tool_calls), len(self.calls)
        return {
            "planned": planned,
            "executed": made,
            "failed": sum(c.status in ("failed", "budget_exceeded") for c in self.calls),
            "retried": sum(c.attempts > 1 for c in self.calls),
            "empty": sum(c.status == "empty_result" for c in self.calls),
            "unplanned": sum(1 for rq in self.match_plan(plan) if not rq),
        }


def _same_args(planned: dict, actual: dict) -> bool:
    norm = lambda d: {k: str(v).strip().lower() for k, v in d.items() if k != "max_results" and v is not None}  # noqa: E731
    return norm(planned) == norm(actual)
