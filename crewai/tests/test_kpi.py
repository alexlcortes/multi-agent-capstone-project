"""The KPI counting rules: tiers, distinct-URL counting, coverage by search result, reproducibility."""

import pytest

from wire3_gtm import kpi

TIERS = {
    "urls": [{"prefix": "https://finance.yahoo.com/quote/T/press-releases", "tier": "primary", "why": "press releases"}],
    "domains": {"att.com": {"tier": "primary", "why": "official"},
                "finance.yahoo.com": {"tier": "weak", "why": "syndication"},
                "cnet.com": {"tier": "top_secondary", "why": "press"}},
}


def ev(i, url, rq="RQ1", excerpt="x"):
    return {"evidence_id": f"EV-0000000{i}", "source_url": url, "research_question_id": rq, "excerpt": excerpt}


def run(evidence, cited, **kw):
    md = " ".join(f"[EV-0000000{i}]" for i in cited)
    return kpi.Run("crewai", "r", evidence, kw.get("analyst", {}), kw.get("strategy", {}), md, kw.get("link_check"), None)


def test_url_rules_beat_domain_rules_and_www_is_ignored():
    assert kpi.tier_of("https://www.att.com/internet", TIERS)[0] == "primary"
    assert kpi.tier_of("https://finance.yahoo.com/quote/T/press-releases", TIERS)[0] == "primary"
    assert kpi.tier_of("https://finance.yahoo.com/news/anything", TIERS)[0] == "weak"


def test_an_unclassified_source_is_an_error_not_a_default():
    with pytest.raises(KeyError, match="no tier rule for example.org"):
        kpi.tier_of("https://example.org/x", TIERS)


def test_every_source_in_the_committed_runs_has_a_tier():
    audit = kpi.classify_all()
    assert audit["records"] and all(r["tier"] in ("primary", "top_secondary", "weak") for r in audit["records"])


def test_source_quality_counts_distinct_urls_not_records():
    evidence = [ev(1, "https://att.com/a"), ev(2, "https://att.com/a", rq="RQ2"), ev(3, "https://cnet.com/b"),
                ev(4, "https://finance.yahoo.com/news/c")]
    q = kpi.source_quality(run(evidence, [1, 2, 3, 4]), TIERS)
    assert (q["cited_urls"], q["cited_records"]) == (3, 4)
    assert q["top_tier_share"] == pytest.approx(2 / 3)  # att (once) + cnet over 3 URLs
    assert q["top_tier_share_by_record"] == pytest.approx(3 / 4)  # the per-record rule, reported alongside


def test_uncited_evidence_does_not_count():
    evidence = [ev(1, "https://att.com/a"), ev(2, "https://finance.yahoo.com/news/c")]
    assert kpi.source_quality(run(evidence, [1]), TIERS)["top_tier_share"] == 1.0


def test_broken_links_are_counted_only_among_cited_urls():
    evidence = [ev(1, "https://att.com/a"), ev(2, "https://cnet.com/b")]
    lc = {"broken": [{"url": "https://cnet.com/b"}, {"url": "https://uncited.example"}], "blocked_urls": [], "unverified_urls": []}
    q = kpi.source_quality(run(evidence, [1, 2], link_check=lc), TIERS)
    assert q["run_time_link_check"]["broken"] == 1 and q["run_time_link_check"]["broken_share"] == 0.5


def test_coverage_counts_any_copy_of_a_cited_search_result():
    evidence = [ev(1, "https://att.com/a", rq="RQ1", excerpt="same"), ev(2, "https://att.com/a", rq="RQ2", excerpt="same"),
                ev(3, "https://cnet.com/b", rq="RQ3")]
    c = kpi.coverage(run(evidence, [1]), ["RQ1", "RQ2", "RQ3"])
    assert c["per_rq"] == {"RQ1": True, "RQ2": True, "RQ3": False} and c["value"] == pytest.approx(2 / 3)


def _decisions(bundle=True, promo=50.0, channels=("digital_paid",)):
    cited = lambda v: {"value": v, "evidence_ids": [], "basis": "evidence"}  # noqa: E731
    analyst = {"competitor_comparison_table": [{"competitor_name": "AT&T", "footprint_confirmed_in_region": cited(True),
                                                "service_type": cited("fiber"), "has_mobile_bundle_available": cited(bundle)}],
               "pricing_matrix": [{"competitor_name": "AT&T", "promo_price_usd_per_month": cited(promo),
                                   "post_promo_price_usd_per_month": cited(90.0)}]}
    strategy = {"channels": [{"channel_category": c} for c in channels], "icps": [{}]}
    return run([], [], analyst=analyst, strategy=strategy)


def test_reproducibility_is_the_mean_share_agreeing_with_the_most_common_answer():
    runs = [_decisions(), _decisions(), _decisions(promo=55.0)]
    r = kpi.reproducibility(runs)
    assert r["per_item"]["AT&T: priced plans (promo, post-promo)"] == pytest.approx(0.67, abs=0.01)
    assert r["per_item"]["AT&T: mobile bundle"] == 1.0
    assert r["value"] < 1.0


def test_reproducibility_needs_three_runs():
    assert kpi.reproducibility([_decisions(), _decisions()]) is None


def test_budget_cap_tests_and_seeded_runs_are_not_counted_for_latency(monkeypatch):
    monkeypatch.setattr(kpi, "current_brief_id", lambda: "b")
    recs = [{"impl": "crewai", "run_id": "run-1", "brief_id": "b", "status": "success", "duration_ms": 600_000, "cost": {"estimated_llm_usd": 0.1}},
            {"impl": "crewai", "run_id": "run-2", "brief_id": "b", "status": "stopped_budget", "duration_ms": 60_000,
             "issues": ["budget overridden for this run: max_wall_clock_minutes=1"]},
            {"impl": "crewai", "run_id": "ab-x-A0", "brief_id": "b", "status": "success", "duration_ms": 1},
            {"impl": "crewai", "run_id": "run-3", "brief_id": "old", "status": "success", "duration_ms": 1}]
    lc = kpi.latency_and_cost(recs, "crewai", 2.5)
    assert (lc["runs_attempted"], lc["runs_completed"], lc["latency_max"], lc["cost_max"]) == (1, 1, 10.0, 0.1)
