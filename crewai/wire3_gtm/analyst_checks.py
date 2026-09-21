"""Checks on an AnalystArtifact against the evidence set it was built from.

Hard (guardrail; a failure re-prompts the Analyst):
  - every cited evidence id exists                       (analyst_models.check_grounding)
  - every RQ that has evidence is cited somewhere or listed in unknowns
Repaired in code (reported): a theme's related RQs are set from the evidence it cites.
Soft (reported, never blocks):
  - a number marked basis 'evidence' that does not appear in the cited text
  - share of cited evidence from top-tier sources
"""

import copy
import json
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


def _rq_number(rq: str | None) -> int:
    m = re.fullmatch(r"RQ(\d+)", rq or "")
    return int(m.group(1)) if m else 10**6


def order_by_rq(evidence: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """RQ1's records first, then RQ2's, ... (stable within a question). Interleaved records made the
    Analyst lose track of which questions it had used."""
    return sorted(evidence, key=lambda e: _rq_number(e.research_question_id))


def rq_checklist(plan, evidence: list[EvidenceRecord]) -> list[dict]:
    """What the Analyst must cover, stated up front instead of buried in a rule. In every recorded
    run the first draft failed the coverage rule (8 of 8), and each rejection cost a full Analyst call."""
    counts = Counter(e.research_question_id for e in evidence)
    return [
        {"research_question_id": q.id, "question": q.question, "evidence_records": counts.get(q.id, 0),
         "requirement": ("cite at least one search result returned for it (the same page appears under several "
                         "evidence_ids, one per question it serves; citing any of them counts for all)"
                         if counts.get(q.id, 0) >= MIN_RECORDS_MUST_CITE
                         else "few records: cite them if they fit, otherwise list it under unknowns")}
        for q in plan.research_questions
    ]


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


def repair_pricing(doc: dict) -> tuple[dict, list[dict]]:
    """Fix the two pricing-rule violations that can be fixed without adding any information.

    A. A Wire3 pricing row without an evidence-cited price cannot be a row at all (Wire3's price is not
       public and the brief forbids inventing it): the row is dropped.
    B. An inferred numeric post-promo price on a promo of 12+ months is set to null. With a 12-month promo
       the post-promo price has zero weight in the 12-month blend, so nothing else changes.

    Deliberately NOT repaired, so the draft goes back for a retry: an inferred price on a plan with no promo
    or a promo under 12 months. There the price is the row's only usable price, so nulling it leaves a row
    that cannot be priced and dropping it would remove a required competitor.

    A repair never adds or changes a claim: it only removes a row or a guess. Returns a repaired COPY and
    a list of every change, so each one can be reported."""
    from pydantic import ValidationError

    from wire3_gtm.analyst_models import PricingMatrixRow

    doc = copy.deepcopy(doc)
    rows = doc.get("pricing_matrix")
    repairs: list[dict] = []
    if not isinstance(rows, list):
        return doc, repairs
    kept = []
    for i, row in enumerate(rows):
        try:
            name, post = row["competitor_name"], row["post_promo_price_usd_per_month"]
            promo_status, months = row["promo_status"]["value"], row["promo_duration_months"]["value"]
        except (KeyError, TypeError):
            kept.append(row)  # malformed: leave it for the strict validator to describe
            continue
        if name == "Wire3":
            try:
                PricingMatrixRow.model_validate(row)
                priceable = True
            except ValidationError:
                priceable = False
            if not priceable or post.get("basis") != "evidence" or post.get("value") is None:
                repairs.append({"repair": "dropped_wire3_pricing_row", "row": i,
                                "reason": "Wire3 has no evidence-cited price, so the row cannot be priced"})
                continue
        elif (post.get("basis") == "inference" and post.get("value") is not None and promo_status == "has_promo"
              and isinstance(months, (int, float)) and months >= 12):
            repairs.append({"repair": "nulled_inferred_post_promo_price", "row": i, "competitor": name,
                            "was": post["value"], "reason": "an inferred price; irrelevant to a 12-month promo's blend"})
            post["value"] = None
        kept.append(row)
    doc["pricing_matrix"] = kept
    return doc, repairs


def _result_key(e: EvidenceRecord) -> tuple[str, str]:
    """One search result = one (url, excerpt). The evidence set holds a separate copy of it, with its
    own id, for every research question the search call served (about 3 copies each)."""
    return (e.source_url, e.excerpt)


def rq_source_depth(artifact: AnalystArtifact, evidence: list[EvidenceRecord]) -> dict[str, int]:
    """For each research question, how many DISTINCT search results the artifact cites that were
    returned for it (a result cited under any of its copies counts for every question it serves)."""
    by_id = {e.evidence_id: e for e in evidence}
    rqs_of: dict[tuple[str, str], set[str]] = {}
    for e in evidence:
        if e.research_question_id:
            rqs_of.setdefault(_result_key(e), set()).add(e.research_question_id)
    used: dict[str, set] = {}
    for i in cited_evidence_ids(artifact.model_dump()):
        if i in by_id:
            for rq in rqs_of.get(_result_key(by_id[i]), ()):
                used.setdefault(rq, set()).add(_result_key(by_id[i]))
    return {rq: len(v) for rq, v in sorted(used.items())}


def coverage_errors(artifact: AnalystArtifact, evidence: list[EvidenceRecord]) -> list[str]:
    """A research question is covered if the artifact cites any search result returned for it, under
    ANY of that result's copies. The first version required the copy tagged with that question's own
    id, which measured which duplicate id the model happened to pick: it rejected the first draft in
    12 of 12 saved drafts, while under this definition 0 of 12 fail and every question was backed by
    at least 5 distinct cited sources."""
    depth = rq_source_depth(artifact, evidence)
    records = Counter(e.research_question_id for e in evidence if e.research_question_id)
    unknown = {u.research_question_id for u in artifact.assumptions_unknowns_conflicts.unknowns}
    errors = []
    for rq in sorted(records):
        if depth.get(rq):
            continue
        if records[rq] >= MIN_RECORDS_MUST_CITE:
            errors.append(
                f"{rq} has {records[rq]} evidence records but none of the search results returned for it is "
                f"cited anywhere in the artifact (an unknowns entry does not excuse an RQ with this much "
                f"evidence): use it"
            )
        elif rq not in unknown:
            errors.append(f"{rq} has {records[rq]} evidence record(s) but is neither cited nor listed in unknowns")
    return errors


def drop_invented_ids(artifact: AnalystArtifact, valid_ids: set[str]) -> tuple[AnalystArtifact, list[dict]]:
    """Remove evidence ids that are not in the evidence set; never remap them.

    Measured on a failed live run: the invented ids were 2, 3, 5 and 5 edits from
    the nearest real id. The 5s were pure hallucinations, and the 2 pointed at an
    unrelated page, so "fix it to the closest id" would attach a real but
    irrelevant source to a claim. Dropping is the honest repair: a field left
    with no citation falls to basis 'inference'.

    Only fields that can legitimately be empty are repaired. A theme's
    supporting_evidence_ids, a derived field's derived_from_evidence_ids and a
    conflict's evidence_id_a/b must keep at least one id, so those are left
    alone and the guardrail rejects them. Returns the repaired artifact and a
    list of every change; the original is returned unchanged if the repair
    would not validate."""
    from pydantic import ValidationError

    doc = artifact.model_dump()
    changes: list[dict] = []

    def walk(node, path):
        if isinstance(node, dict):
            ids = node.get("evidence_ids")
            if isinstance(ids, list):
                kept = [i for i in ids if i in valid_ids]
                if len(kept) != len(ids):
                    downgraded = node.get("basis") == "evidence" and not kept
                    changes.append({"path": path, "dropped": [i for i in ids if i not in valid_ids],
                                    "basis_downgraded_to_inference": downgraded})
                    node["evidence_ids"] = kept
                    if downgraded:
                        node["basis"] = "inference"
            for key in ("supporting_evidence_ids", "derived_from_evidence_ids"):
                ids = node.get(key)
                if isinstance(ids, list):
                    kept = [i for i in ids if i in valid_ids]
                    if kept and len(kept) != len(ids):
                        changes.append({"path": f"{path}.{key}" if path else key,
                                        "dropped": [i for i in ids if i not in valid_ids],
                                        "basis_downgraded_to_inference": False})
                        node[key] = kept
            for k, v in node.items():
                walk(v, f"{path}.{k}" if path else k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")

    walk(doc, "")
    if not changes:
        return artifact, []
    try:
        return AnalystArtifact.model_validate(doc), changes
    except ValidationError:
        return artifact, []


def invalid_reason(model, raw: str) -> str:
    """Why `raw` is not a valid `model`, in words the LLM can act on. CrewAI hands
    the guardrail pydantic=None on a schema failure, which alone would only say
    'return a valid object'."""
    from pydantic import ValidationError

    try:
        model.model_validate_json(raw)
    except ValidationError as exc:
        details = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:8])
        return f"Your output is not a valid {model.__name__}. Fix and return the full object. Errors: {details}"
    except ValueError as exc:
        return f"Your output is not valid JSON for {model.__name__}: {exc}"
    return f"Return a valid {model.__name__} object."


def make_analyst_guardrail(evidence: list[EvidenceRecord], dropped_log: list | None = None,
                           degrade=None, gaps_log: list | None = None, pricing_log: list | None = None):
    """dropped_log receives the ids dropped from the most recent attempt.

    degrade: optional callable, True when there is no time left for another attempt. If the ONLY
    remaining problem is uncovered research questions, the draft is then accepted instead of
    re-prompted (or the run stopped), and the gaps go to gaps_log so the run is reported degraded
    and the document says so. Any other problem is never accepted."""
    valid_ids = set(_rq_by_id(evidence))

    def guardrail(output):
        from wire3_gtm.wire_models import strict_or_feedback

        if pricing_log is not None:
            pricing_log.clear()
        raw, repairs = output.raw, []
        try:
            doc, repairs = repair_pricing(json.loads(output.raw))
            if repairs:
                raw = json.dumps(doc)
        except (ValueError, TypeError):
            pass  # not JSON: strict_or_feedback below reports it
        artifact, feedback = strict_or_feedback(AnalystArtifact, output, raw)  # custom rules run here, not in the SDK
        if artifact is None:
            return False, feedback
        if pricing_log is not None:
            pricing_log.extend(repairs)
        output.pydantic = artifact
        artifact, dropped = drop_invented_ids(artifact, valid_ids)
        if dropped:
            output.pydantic = artifact
        if dropped_log is not None:
            dropped_log[:] = dropped
        grounding, coverage = check_grounding(artifact, valid_ids), coverage_errors(artifact, evidence)
        if gaps_log is not None:
            gaps_log.clear()
        if coverage and not grounding and degrade is not None and degrade():
            if gaps_log is not None:
                gaps_log.extend(coverage)
            return True, output  # a valid, grounded artifact with uncovered questions, and no time to redo it
        errors = grounding + coverage
        if errors:
            return False, (
                "Fix these problems and return the full corrected artifact. Copy evidence ids only "
                "from the evidence set, character for character (or use basis 'inference' with empty "
                "evidence_ids); every RQ with 5+ evidence records must be cited (unknowns do not "
                "excuse it). Problems: " + " | ".join(errors[:15])
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
