from __future__ import annotations

from datetime import datetime, timezone

from .factory import get_provider


def _retrieval_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    return _run(f"{company_name} company overview business model", max_results)


def competitor_discovery(company_name: str, region: str, max_results: int = 10) -> list[dict]:
    """Who competes with company_name in a given region/market."""
    return _run(f"{company_name} competitors in {region}", max_results)


def product_portfolio_mapping(company_name: str, max_results: int = 10) -> list[dict]:
    """Product lines, plans, and tiers a company currently offers."""
    return _run(f"{company_name} products plans tiers", max_results)


def pricing_research(company_name: str, region: str, max_results: int = 10) -> list[dict]:
    """Current pricing, promo rates, and post-promo rates for a company in a region."""
    return _run(f"{company_name} pricing plans promo rate {region}", max_results)


def recent_news(company_name: str, max_results: int = 10) -> list[dict]:
    """Recent news and press coverage about a company."""
    return _run(f"{company_name} news", max_results)
