"""Checks on a StrategyArtifact against the AnalystArtifact it was built from.

Hard (guardrail; a failure re-prompts the Strategy Agent):
  - every supporting_id is an id that really appears in the Analyst artifact
  - every Analyst unknown is picked up by a follow-up research question
Reported (never blocks):
  - basis mix, and Wire3-response fields resting on 'evidence'
"""

import re
from collections import Counter

from wire3_gtm.analyst_models import AnalystArtifact, dump_contract
from wire3_gtm.strategy_models import StrategyArtifact


def analyst_ids(analyst: AnalystArtifact) -> dict[str, set[str]]:
    """Ids Strategy may cite, from the Analyst artifact itself: the evidence ids
    it cites (not the whole evidence set), its themes, SWOT items, assumptions
    and unknowns. CONF-n is excluded because the schema's SupportingRef does
    not allow it."""
    from wire3_gtm.analyst_models import cited_evidence_ids

    doc = dump_contract(analyst)
    return {
        "EV": cited_evidence_ids(doc),
        "THEME": {t["theme_id"] for t in doc["market_themes"]},
        "SWOT": {i["item_id"] for items in doc["swot"].values() for i in items},
        "ASM": {a["id"] for a in doc["assumptions_unknowns_conflicts"]["assumptions"]},
        "UNK": {u["id"] for u in doc["assumptions_unknowns_conflicts"]["unknowns"]},
    }


def _all_ids(ids: dict[str, set[str]]) -> set[str]:
    return set().union(*ids.values())


def _walk_cited(node, path=""):
    if isinstance(node, dict):
        if {"value", "supporting_ids", "basis"} <= node.keys():
            yield path, node
        for k, v in node.items():
            yield from _walk_cited(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk_cited(v, f"{path}[{i}]")


def grounding_errors(strategy: StrategyArtifact, analyst: AnalystArtifact) -> list[str]:
    valid = _all_ids(analyst_ids(analyst))
    errors = []
    for path, field in _walk_cited(dump_contract(strategy)):
        bad = [i for i in field["supporting_ids"] if i not in valid]
        if bad:
            errors.append(f"{path} cites {bad}, which are not ids in the Analyst artifact")
    from wire3_gtm.analyst_models import prose_id_errors

    return errors + prose_id_errors(dump_contract(strategy), {i for i in valid if i.startswith("EV-")})


def unknown_coverage_errors(strategy: StrategyArtifact, analyst: AnalystArtifact) -> list[str]:
    """n8n rule 10: each unresolved Analyst unknown gets a follow-up question."""
    linked = {u for f in strategy.follow_up_research_questions for u in f.related_unknown_ids}
    return [
        f"Analyst unknown {u} has no follow-up research question (set related_unknown_ids to include it)"
        for u in sorted(analyst_ids(analyst)["UNK"] - linked)
    ]


def make_strategy_guardrail(analyst: AnalystArtifact):
    def guardrail(output):
        from wire3_gtm.wire_models import strict_or_feedback

        strategy, feedback = strict_or_feedback(StrategyArtifact, output)  # custom rules run here, not in the SDK
        if strategy is None:
            return False, feedback
        output.pydantic = strategy
        errors = grounding_errors(strategy, analyst) + unknown_coverage_errors(strategy, analyst)
        if errors:
            return False, (
                "Fix these problems and return the full corrected artifact. supporting_ids must be "
                "copied from ids in the Analyst artifact (EV-, THEME-, SWOT-, ASM-, UNK-); your own "
                "ICP-/PAIN-/PILLAR-/CHANNEL-/PHASE-/METRIC-/RISK-/FRQ- ids are never sources. Use "
                "basis 'inference' with empty supporting_ids when nothing real applies. Problems: "
                + " | ".join(errors[:15])
            )
        return True, output

    return guardrail


def report(strategy: StrategyArtifact) -> dict:
    """Basis mix, plus Wire3-response fields that claim 'evidence' (competitor
    research cannot tell you how Wire3 should respond, so that is suspect)."""
    fields = list(_walk_cited(dump_contract(strategy)))
    mix = Counter(f["basis"] for _, f in fields)
    vp = strategy.value_proposition
    suspect = [
        name for name, f in (
            ("no_bundle_gap_response", vp.no_bundle_gap_response),
            ("low_brand_recognition_response", vp.low_brand_recognition_response),
        ) if f.basis == "evidence"
    ]
    return {"cited_fields": len(fields), "basis_mix": dict(mix), "wire3_response_claims_evidence": suspect}
