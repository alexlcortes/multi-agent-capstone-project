from test_analyst_models import EV, valid  # noqa: F401  (fixture reused)

from wire3_gtm.analyst_checks import (
    coverage_errors, numbers_in, repair_theme_rqs, source_quality, theme_rq_errors, unsupported_number_flags,
)
from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.evidence import EvidenceRecord


def rec(eid, rq, excerpt="plain text", source_type="company"):
    return EvidenceRecord(
        evidence_id=eid, research_question_id=rq, claim=excerpt, source_title="t",
        source_url="https://x.example", source_type=source_type,
        retrieval_timestamp="2026-09-20T00:00:00Z", excerpt=excerpt,
    )


def art(valid):
    return AnalystArtifact.model_validate(valid)


def test_numbers_read_gbps_as_mbps():
    assert 10000.0 in numbers_in("up to 10 Gbps symmetrical")
    assert 2000.0 in numbers_in("2 gig plan") and 49.99 in numbers_in("$49.99/mo")


def test_theme_rqs_are_repaired_from_cited_evidence_and_reported(valid):
    evidence = [rec(EV[0], "RQ1"), rec(EV[1], "RQ2")]
    artifact = art(valid)  # themes claim RQ2 but cite EV-aaaaaaaa (RQ1)
    assert len(theme_rq_errors(artifact, evidence)) == 3
    repairs = repair_theme_rqs(artifact, evidence)
    assert len(repairs) == 3 and repairs[0]["before"] == ["RQ2"] and repairs[0]["after"] == ["RQ1"]
    assert theme_rq_errors(artifact, evidence) == []
    assert repair_theme_rqs(artifact, evidence) == []  # idempotent


def test_rq_with_plenty_of_evidence_cannot_be_an_unknown(valid):
    many = [rec(EV[0], "RQ2")] + [rec(f"EV-cccccc{i:02d}", "RQ8") for i in range(5)]
    valid["assumptions_unknowns_conflicts"]["unknowns"] = [
        {"id": "UNK-1", "research_question_id": "RQ8", "description": "d", "reason_unresolved": "r"}]
    errs = coverage_errors(art(valid), many)
    assert len(errs) == 1 and "RQ8" in errs[0] and "does not excuse" in errs[0]


def test_thin_rq_may_be_an_unknown_but_must_be_accounted_for(valid):
    thin = [rec(EV[0], "RQ2"), rec(EV[1], "RQ8")]  # 1 record for RQ8, never cited
    assert "RQ8" in coverage_errors(art(valid), thin)[0]
    valid["assumptions_unknowns_conflicts"]["unknowns"] = [
        {"id": "UNK-1", "research_question_id": "RQ8", "description": "d", "reason_unresolved": "r"}]
    assert coverage_errors(art(valid), thin) == []


def test_unsupported_number_is_flagged_but_supported_is_not(valid):
    # every cited field in the fixture cites EV-aaaaaaaa; speed 1000 is 'evidence' basis
    flags = unsupported_number_flags(art(valid), [rec(EV[0], "RQ2", "nothing numeric")])
    assert any(f["path"].endswith("top_advertised_speed_mbps_down") for f in flags)
    flags = unsupported_number_flags(art(valid), [rec(EV[0], "RQ2", "speeds of 1 Gbps, 300 Mbps, 30 dollars, 60")])
    assert not any(f["path"].endswith("top_advertised_speed_mbps_down") for f in flags)


def test_source_quality_share(valid):
    q = source_quality(art(valid), [rec(EV[0], "RQ2", source_type="community")])
    assert q["top_tier_share"] == 0.0 and not q["meets_80pct_target"]
    q = source_quality(art(valid), [rec(EV[0], "RQ2", source_type="primary")])
    assert q["meets_80pct_target"]
