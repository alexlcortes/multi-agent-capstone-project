from __future__ import annotations

from datetime import datetime, timezone

import httpx

from .factory import get_provider


def _retrieval_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _require(value: str, field_name: str) -> str:
    """Rejects blank required fields before they reach the provider -- an
    empty company_name would otherwise silently become a degenerate query
    instead of a clear error."""
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty")
    return cleaned


def _run(query: str, max_results: int) -> list[dict]:
    """Shared call path for all five tools. Only this function touches a
    SearchProvider -- swapping providers never changes any tool below."""
    provider = get_provider()
    retrieval_timestamp = _retrieval_timestamp()
    return [
        {
            "source_title": result.title,
            "source_url": result.url,
            "excerpt": result.snippet,
            "publication_date": result.published_date,
            "retrieval_timestamp": retrieval_timestamp,
        }
        for result in provider.search(query, max_results=max_results)
    ]


def company_overview(company_name: str, max_results: int = 10) -> list[dict]:
    """Business model, market position, and background for one company."""
    company_name = _require(company_name, "company_name")
    return _run(f"{company_name} company overview business model", max_results)


def competitor_discovery(company_name: str, region: str, max_results: int = 10) -> list[dict]:
    """Who competes with company_name in a given region/market."""
    company_name = _require(company_name, "company_name")
    region = _require(region, "region")
    return _run(f"{company_name} competitors in {region}", max_results)


def product_portfolio_mapping(company_name: str, max_results: int = 10) -> list[dict]:
    """Product lines, plans, and tiers a company currently offers."""
    company_name = _require(company_name, "company_name")
    return _run(f"{company_name} products plans tiers", max_results)


def pricing_research(company_name: str, region: str, max_results: int = 10) -> list[dict]:
    """Current pricing, promo rates, and post-promo rates for a company in a region."""
    company_name = _require(company_name, "company_name")
    region = _require(region, "region")
    return _run(f"{company_name} pricing plans promo rate {region}", max_results)


def recent_news(company_name: str, max_results: int = 10) -> list[dict]:
    """Recent news and press coverage about a company."""
    company_name = _require(company_name, "company_name")
    return _run(f"{company_name} news", max_results)


def validate_source(url: str, timeout: float = 10.0) -> dict:
    """Checks whether a cited source URL is still reachable.

    A broken link is a normal, expected result (is_valid False) -- it's the
    thing this tool exists to detect, not a tool failure. Only being unable
    to attempt the check at all (a malformed/empty url) raises. A network
    error while checking is caught and reported as is_valid False with an
    error message, the same as a 404, so the Analyst/Docs Writer steps can
    flag it without the whole pipeline run failing on one dead link.
    """
    url = _require(url, "url")
    checked_at = _retrieval_timestamp()
    try:
        response = httpx.head(url, timeout=timeout, follow_redirects=True)
        return {
            "url": url,
            "is_valid": response.status_code < 400,
            "status_code": response.status_code,
            "error": None,
            "checked_at": checked_at,
        }
    except httpx.HTTPError as exc:
        return {
            "url": url,
            "is_valid": False,
            "status_code": None,
            "error": str(exc),
            "checked_at": checked_at,
        }
