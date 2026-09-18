from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from research.adapter import SearchResult
from research.tools import (
    company_overview,
    competitor_discovery,
    pricing_research,
    product_portfolio_mapping,
    recent_news,
    validate_source,
)

SAMPLE_RESULTS = [
    SearchResult(
        title="Wire 3 Expands to Marion County",
        url="https://wire3.com/wire-3-expands-to-marion-county",
        snippet="Wire 3 is a leading fiber optic internet provider in Central Florida.",
        published_date="2026-08-06",
    ),
    SearchResult(
        title="About - Wire 3",
        url="https://wire3.com/about",
        snippet="Wire 3 isn't a national conglomerate that happened to lay fiber nearby.",
        published_date=None,
    ),
]


class FakeProvider:
    """Stands in for a real SearchProvider so tests never hit the network."""

    def __init__(self, results=None, error=None):
        self._results = results or []
        self._error = error

    def search(self, query: str, *, max_results: int = 10):
        if self._error is not None:
            raise self._error
        return self._results[:max_results]


def _patched(provider):
    # Patched where it's looked up (research.tools), not where it's defined
    # (research.factory) -- tools.py imports the name directly.
    return patch("research.tools.get_provider", return_value=provider)


# ---------- company_overview ----------


def test_company_overview_normal_request():
    with _patched(FakeProvider(SAMPLE_RESULTS)):
        results = company_overview("Wire3", max_results=2)

    assert len(results) == 2
    assert results[0]["source_title"] == "Wire 3 Expands to Marion County"
    assert results[0]["source_url"] == "https://wire3.com/wire-3-expands-to-marion-county"
    assert results[0]["publication_date"] == "2026-08-06"
    assert results[1]["publication_date"] is None
    assert results[0]["retrieval_timestamp"].endswith("Z")


def test_company_overview_optional_field_omitted():
    # max_results is optional; omitting it must not raise and must fall back
    # to the documented default (10) rather than requiring the caller to know it.
    with _patched(FakeProvider(SAMPLE_RESULTS)):
        results = company_overview("Wire3")

    assert len(results) == 2  # FakeProvider only has 2 to give either way


def test_company_overview_empty_company_name_is_rejected():
    with pytest.raises(ValueError):
        company_overview("")


def test_company_overview_provider_failure_propagates():
    with _patched(FakeProvider(error=RuntimeError("provider unavailable"))):
        with pytest.raises(RuntimeError):
            company_overview("Wire3")


# ---------- competitor_discovery ----------


def test_competitor_discovery_normal_request():
    with _patched(FakeProvider(SAMPLE_RESULTS)):
        results = competitor_discovery("Wire3", "Ocala, FL", max_results=2)

    assert len(results) == 2
    assert results[0]["source_url"].startswith("https://")


def test_competitor_discovery_optional_field_omitted():
    with _patched(FakeProvider(SAMPLE_RESULTS)):
        results = competitor_discovery("Wire3", "Ocala, FL")

    assert len(results) == 2


def test_competitor_discovery_empty_region_is_rejected():
    with pytest.raises(ValueError):
        competitor_discovery("Wire3", "")


def test_competitor_discovery_provider_failure_propagates():
    with _patched(FakeProvider(error=RuntimeError("provider unavailable"))):
        with pytest.raises(RuntimeError):
            competitor_discovery("Wire3", "Ocala, FL")


# ---------- product_portfolio_mapping ----------


def test_product_portfolio_mapping_normal_request():
    with _patched(FakeProvider(SAMPLE_RESULTS)):
        results = product_portfolio_mapping("Wire3", max_results=2)

    assert len(results) == 2
    assert results[0]["source_title"] == "Wire 3 Expands to Marion County"
    assert results[0]["source_url"].startswith("https://")


def test_product_portfolio_mapping_optional_field_omitted():
    with _patched(FakeProvider(SAMPLE_RESULTS)):
        results = product_portfolio_mapping("Wire3")

    assert len(results) == 2


def test_product_portfolio_mapping_empty_company_name_is_rejected():
    with pytest.raises(ValueError):
        product_portfolio_mapping("")


def test_product_portfolio_mapping_provider_failure_propagates():
    with _patched(FakeProvider(error=RuntimeError("provider unavailable"))):
        with pytest.raises(RuntimeError):
            product_portfolio_mapping("Wire3")


# ---------- pricing_research ----------


def test_pricing_research_normal_request():
    with _patched(FakeProvider(SAMPLE_RESULTS)):
        results = pricing_research("Wire3", "Ocala, FL", max_results=2)

    assert len(results) == 2
    assert results[0]["source_url"].startswith("https://")


def test_pricing_research_optional_field_omitted():
    with _patched(FakeProvider(SAMPLE_RESULTS)):
        results = pricing_research("Wire3", "Ocala, FL")

    assert len(results) == 2


def test_pricing_research_empty_region_is_rejected():
    with pytest.raises(ValueError):
        pricing_research("Wire3", "")


def test_pricing_research_provider_failure_propagates():
    with _patched(FakeProvider(error=RuntimeError("provider unavailable"))):
        with pytest.raises(RuntimeError):
            pricing_research("Wire3", "Ocala, FL")


# ---------- recent_news ----------


def test_recent_news_normal_request():
    with _patched(FakeProvider(SAMPLE_RESULTS)):
        results = recent_news("Wire3", max_results=2)

    assert len(results) == 2
    assert results[0]["source_url"].startswith("https://")


def test_recent_news_optional_field_omitted():
    with _patched(FakeProvider(SAMPLE_RESULTS)):
        results = recent_news("Wire3")

    assert len(results) == 2


def test_recent_news_empty_company_name_is_rejected():
    with pytest.raises(ValueError):
        recent_news("")


def test_recent_news_provider_failure_propagates():
    with _patched(FakeProvider(error=RuntimeError("provider unavailable"))):
        with pytest.raises(RuntimeError):
            recent_news("Wire3")


# ---------- validate_source ----------
#
# Unlike the search-backed tools above, a "provider failure" here (a network
# error while checking the link) is not re-raised -- it's reported as
# is_valid False, the same shape as a 404, so one dead citation can't fail
# an entire pipeline run.


class FakeHeadResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


def _patched_head(response=None, error=None):
    if error is not None:
        return patch("research.tools.httpx.head", side_effect=error)
    return patch("research.tools.httpx.head", return_value=response)


def test_validate_source_normal_request():
    with _patched_head(FakeHeadResponse(200)):
        result = validate_source("https://wire3.com/pricing")

    assert result["is_valid"] is True
    assert result["status_code"] == 200
    assert result["error"] is None
    assert result["checked_at"].endswith("Z")


def test_validate_source_optional_field_omitted():
    # timeout is optional; omitting it must not raise and must use the
    # documented default (10.0) rather than requiring the caller to know it.
    with _patched_head(FakeHeadResponse(200)) as mock_head:
        validate_source("https://wire3.com/pricing")

    assert mock_head.call_args.kwargs["timeout"] == 10.0


def test_validate_source_empty_url_is_rejected():
    with pytest.raises(ValueError):
        validate_source("")


def test_validate_source_broken_link_is_reported_not_raised():
    with _patched_head(error=httpx.ConnectError("connection refused")):
        result = validate_source("https://wire3.com/dead-link")

    assert result["is_valid"] is False
    assert result["status_code"] is None
    assert "connection refused" in result["error"]
