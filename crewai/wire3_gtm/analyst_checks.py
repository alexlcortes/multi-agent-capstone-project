"""Checks on an AnalystArtifact against the evidence set it was built from.

Hard (guardrail; a failure re-prompts the Analyst):
  - every cited evidence id exists                       (analyst_models.check_grounding)
  - every RQ that has evidence is cited somewhere or listed in unknowns
Repaired in code (reported): a theme's related RQs are set from the evidence it cites.
Soft (reported, never blocks):
  - a number marked basis 'evidence' that does not appear in the cited text
  - share of cited evidence from top-tier sources
"""

import re
from collections import Counter

from wire3_gtm.analyst_models import AnalystArtifact, check_grounding, cited_evidence_ids, dump_contract
from wire3_gtm.evidence import EvidenceRecord

# Brief KPI: >=80% primary/top-tier sources. Defined here as primary + company
# (provider's own pages) + analyst (J.D. Power, Parks, ...). news and community
# are reported but do not count as top-tier.
TOP_TIER = {"primary", "company", "analyst"}
MIN_TOP_TIER_SHARE = 0.80


def _rq_by_id(evidence: list[EvidenceRecord]) -> dict[str, str | None]:
    return {e.evidence_id: e.research_question_id for e in evidence}


def theme_rq_errors(artifact: AnalystArtifact, evidence: list[EvidenceRecord]) -> list[str]:
    rq_of = _rq_by_id(evidence)
    errors = []
    for t in artifact.market_themes:
        cited_rqs = {rq_of.get(i) for i in t.supporting_evidence_ids}
        off = sorted(set(t.related_research_question_ids) - cited_rqs)
        if off:
            errors.append(
                f"{t.theme_id} lists {off} in related_research_question_ids but none of its "
                f"supporting_evidence_ids come from those questions (they come from "
                f"{sorted(x for x in cited_rqs if x)})"
            )
    return errors


# An RQ with at least this many evidence records cannot be waved off as an
# "unknown": the evidence exists, so the Analyst has to use it. Below this, an
# unknown entry is an acceptable account of a thin RQ. (Live run: every RQ had
# 27-64 records, and the Analyst had marked RQ3/5/6/7/8 unknown anyway.)
MIN_RECORDS_MUST_CITE = 5


def repair_theme_rqs(artifact: AnalystArtifact, evidence: list[EvidenceRecord]) -> list[dict]:
    """A theme's related RQs are fully determined by the evidence it cites, so
    set them from that evidence in code instead of asking the LLM to hand-copy
    them (live run: retries fixed the flagged themes and broke others until the
    guardrail gave up). Returns every change made so it stays visible."""
    rq_of = _rq_by_id(evidence)
    repairs = []
    for t in artifact.market_themes:
        derived = sorted({rq_of[i] for i in t.supporting_evidence_ids if rq_of.get(i)})
        if derived and derived != sorted(t.related_research_question_ids):
            repairs.append({"theme_id": t.theme_id, "before": t.related_research_question_ids, "after": derived})
            t.related_research_question_ids = derived
    return repairs


def coverage_errors(artifact: AnalystArtifact, evidence: list[EvidenceRecord]) -> list[str]:
    rq_of = _rq_by_id(evidence)
    records = Counter(rq for rq in rq_of.values() if rq)
    cited = {rq_of.get(i) for i in cited_evidence_ids(artifact.model_dump())}
    unknown = {u.research_question_id for u in artifact.assumptions_unknowns_conflicts.unknowns}
    errors = []
    for rq in sorted(records):
        if rq in cited:
            continue
        if records[rq] >= MIN_RECORDS_MUST_CITE:
            errors.append(
                f"{rq} has {records[rq]} evidence records but nothing in the artifact cites any of them "
                f"(an unknowns entry does not excuse an RQ with this much evidence): use it"
            )
        elif rq not in unknown:
            errors.append(f"{rq} has {records[rq]} evidence record(s) but is neither cited nor listed in unknowns")
    return errors


def make_analyst_guardrail(evidence: list[EvidenceRecord]):
    valid_ids = set(_rq_by_id(evidence))

    def guardrail(output):
        artifact = output.pydantic
        if artifact is None:
            return False, "Return a valid AnalystArtifact object."
        errors = (
            check_grounding(artifact, valid_ids)
            + coverage_errors(artifact, evidence)
        )
        if errors:
            return False, (
                "Fix these problems and return the full corrected artifact. Copy evidence ids only "
                "from the evidence set (or use basis 'inference' with empty evidence_ids); theme RQs "
                "must come from the evidence the theme cites; every RQ with 5+ evidence records must be cited "
                "(unknowns do not excuse it). Problems: " + " | ".join(errors[:15])
            )
        return True, output

    return guardrail


# --- soft checks --------------------------------------------------------------

_NUM = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(gbps|gig(?:abit)?s?|g\b)?", re.I)


def numbers_in(text: str) -> set[float]:
    """Numbers in text, with '10 Gbps' / '2 gig' also read as 10000 / 2000 Mbps."""
    out: set[float] = set()
    for raw, unit in _NUM.findall(text):
        n = float(raw.replace(",", ""))
        out.add(n)
        if unit:
            out.add(n * 1000)
    return out


def _walk_cited(node, path=""):
    if isinstance(node, dict):
        if {"value", "evidence_ids", "basis"} <= node.keys():
            yield path, node
        for k, v in node.items():
            yield from _walk_cited(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_cited(v, f"{path}[{i}]")


def unsupported_number_flags(artifact: AnalystArtifact, evidence: list[EvidenceRecord]) -> list[dict]:
    """Numbers marked basis 'evidence' whose value appears in none of the cited
    excerpts/claims. A flag means 'a human should look', not 'wrong': the text
    may state the figure in words or a form this matcher does not read."""
    by_id = {e.evidence_id: e for e in evidence}
    flags = []
    for path, field in _walk_cited(dump_contract(artifact)):
        value = field["value"]
        if field["basis"] != "evidence" or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        text = " ".join(f"{by_id[i].excerpt} {by_id[i].claim}" for i in field["evidence_ids"] if i in by_id)
        if float(value) not in numbers_in(text):
            flags.append({"path": path, "value": value, "evidence_ids": field["evidence_ids"]})
    return flags


def source_quality(artifact: AnalystArtifact, evidence: list[EvidenceRecord]) -> dict:
    by_id = {e.evidence_id: e for e in evidence}
    cited = [by_id[i] for i in cited_evidence_ids(artifact.model_dump()) if i in by_id]
    counts = Counter(e.source_type for e in cited)
    share = sum(counts[t] for t in TOP_TIER) / len(cited) if cited else 0.0
    return {
        "cited_records": len(cited),
        "by_source_type": dict(counts),
        "top_tier_share": round(share, 3),
        "meets_80pct_target": share >= MIN_TOP_TIER_SHARE,
    }
