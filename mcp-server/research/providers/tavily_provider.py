from __future__ import annotations

import os

import httpx

from ..adapter import SearchProvider, SearchResult

_ENDPOINT = "https://api.tavily.com/search"


class TavilyProvider(SearchProvider):
    provider_name = "tavily"

    def __init__(self) -> None:
        self._api_key = os.environ["TAVILY_API_KEY"]

    def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        response = httpx.post(
            _ENDPOINT,
            json={
                "api_key": self._api_key,
                "query": query,
                "max_results": max_results,
            },
            timeout=15.0,
        )
        response.raise_for_status()
        results = response.json().get("results", [])
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                # Tavily only returns published_date for some query topics (e.g.
                # topic="news"); absent, not inferred, matches publication_date's
                # nullable contract.
                published_date=item.get("published_date"),
            )
            for item in results[:max_results]
        ]
