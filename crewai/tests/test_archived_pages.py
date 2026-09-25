"""The brief's provider pricing pages are read from Wayback Machine snapshots as dated RQ2 evidence."""

import json

import yaml
from crewai.tools import BaseTool
from pydantic import BaseModel
from test_brief_context import LAKE
from test_analyst_models import cited, valid  # noqa: F401 (valid is a fixture)
from test_census_research import adapter_with, setup
from test_plan_enforcement import call, plan_of

from wire3_gtm.agents import CONFIG_DIR
from wire3_gtm.analyst_checks import archived_price_errors
from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.brief_context import OCALA, fill_config, load, using
from wire3_gtm.evidence import EvidenceRecord, classify_source_type
from wire3_gtm.research_tools import ARCHIVE_TOOL, run_archived_pages

SNAP = "https://web.archive.org/web/20260518232607/https://www.xfinity.com/local/fl/mount-dora"


class UrlArgs(BaseModel):
    url: str
    before: str | None = None


def fake_archive(log):
    class Archive(BaseTool):
        name: str = ARCHIVE_TOOL
        description: str = "d"
        args_schema: type = UrlArgs

        def _run(self, url: str, before: str | None = None) -> str:
            log.append(url)
            return json.dumps([{"source_title": "Xfinity Mount Dora (Wayback Machine snapshot, 2026-05-18)",
                                "source_url": SNAP, "publication_date": "2026-05-18",
                                "excerpt": "Archived 2026-05-18: 300 Mbps | $45/mo - Special offer pricing | $75/mo - Everyday pricing",
                                "retrieval_timestamp": "2026-09-24T00:00:00Z"}])

    return Archive()


def test_lake_brief_lists_six_pages_for_rq2_and_moves_wayback_to_preferred():
    assert LAKE.archived_rq == "RQ2" and len(LAKE.archived_pages) == 6 and OCALA.archived_pages == ()
    sources = load("lake-county")["sources"]
    assert "Wayback Machine" not in sources["excluded"] and any("Wayback" in s for s in sources["preferred"])


def test_snapshots_are_classified_by_the_page_they_archive():
    assert classify_source_type(SNAP) == classify_source_type("https://www.xfinity.com/local/fl/mount-dora")
    assert classify_source_type("https://web.archive.org/web/20260101000000/https://www.fcc.gov/x") == "primary"


def test_archived_page_evidence_is_dated_and_tagged_rq2(tmp_path):
    collector, monitor, policy = setup(tmp_path)
    log = []
    run_archived_pages(("https://www.xfinity.com/local/fl/mount-dora",), "RQ2", collector, monitor=monitor,
                       policy=policy, adapter=adapter_with(fake_archive(log)))
    (e,) = collector.build_evidence(plan_of(call("recent_news", "Wire3")))
    assert log == ["https://www.xfinity.com/local/fl/mount-dora"] and collector.calls[0].source == "archive"
    assert e.research_question_id == "RQ2" and e.source_url == SNAP and e.publication_date == "2026-05-18"
    assert "$75/mo - Everyday pricing" in e.claim


def test_the_analyst_is_told_how_to_read_archived_prices_for_lake_only():
    def analyst(ctx):
        with using(ctx):
            return fill_config(yaml.safe_load((CONFIG_DIR / "tasks.yaml").read_text()))["analyze_evidence"]["description"]
    assert "web.archive.org" in analyst(LAKE) and "'Everyday pricing'" in analyst(LAKE)
    assert "lock" not in analyst(LAKE).split("web.archive.org")[1]  # a price lock is not a promo length
    assert "web.archive.org" not in analyst(OCALA)


def test_lake_competitor_sites_are_company_sources_and_ocala_is_unchanged():
    for url in ("https://www.xfinity.com/local/fl/mount-dora", SNAP, "https://www.verizon.com/home/internet/5g",
                "https://www.quantumfiber.com/", "https://www.centurylink.com/home/internet.html"):
        with using(LAKE):
            assert classify_source_type(url) == "company", url
    assert classify_source_type("https://www.xfinity.com/local/fl/mount-dora") == "community"  # Ocala, as before
    with using(LAKE):
        assert classify_source_type("https://notxfinity.com/deals") == "community"


def _record(eid, url):
    return EvidenceRecord(evidence_id=eid, research_question_id="RQ2", claim="c", source_title="t", source_url=url,
                          source_type="company", retrieval_timestamp="2026-09-24T00:00:00Z", excerpt="$75/mo")


def test_lake_maps_each_archived_page_to_its_competitor():
    assert set(LAKE.archived_page_of) == set(LAKE.competitors) and OCALA.archived_page_of == {}
    assert LAKE.archived_page_of["Xfinity"] == "https://www.xfinity.com/local/fl/mount-dora"


def test_a_competitor_must_be_priced_from_its_archived_page(valid):
    page = "https://www.spectrum.com/locations/internet-wifi-service/mount-dora-fl-i09117"
    evidence = [_record("EV-aaaaaaaa", "https://www.spectrum.com/deals"),
                _record("EV-cccccccc", f"https://web.archive.org/web/20260417093007/{page}/")]
    artifact = AnalystArtifact.model_validate(valid)  # every row cites EV-aaaaaaaa, not the archived page
    (error,) = archived_price_errors(artifact, evidence, {"Spectrum": page})
    assert "Spectrum" in error and "EV-cccccccc" in error
    valid["pricing_matrix"][-1]["post_promo_price_usd_per_month"] = cited(75.0, ids=["EV-cccccccc"])  # one row suffices
    assert archived_price_errors(AnalystArtifact.model_validate(valid), evidence, {"Spectrum": page}) == []


def test_an_archived_page_that_returned_nothing_is_not_required(valid):
    artifact = AnalystArtifact.model_validate(valid)
    assert archived_price_errors(artifact, [], {"Spectrum": "https://www.spectrum.com/x"}) == []
