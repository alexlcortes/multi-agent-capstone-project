from __future__ import annotations

import os
from datetime import datetime

import httpx

from ..adapter import SearchProvider, SearchResult

_ENDPOINT = "https://serpapi.com/search"

# SerpAPI's "date" field is a locale-dependent display string, not ISO 8601 --
# without hl/gl pinned, Google can return it in another locale's date order
# (e.g. "10‏/08‏/2026" with embedded bidi marks), which is ambiguous DD/MM vs
# MM/DD and unparseable as-is. Pinning en/us gets a consistent "Mon D, YYYY".
_LOCALE_PARAMS = {"hl": "en", "gl": "us"}
_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y")


def _normalize_date(raw: str | None) -> str | None:
    """Best-effort parse of SerpAPI's date string to ISO 8601. Returns None
    (unknown, not guessed) for formats we don't recognize, e.g. relative
    dates like "3 days ago" -- matches publication_date's nullable contract."""
    if not raw:
        return None
    cleaned = raw.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date().isoformat()
        except ValueError:
            continue
    return None


class SerpAPIProvider(SearchProvider):
    provider_name = "serpapi"

    def __init__(self) -> None:
        self._api_key = os.environ["SERPAPI_API_KEY"]

    def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        response = httpx.get(
            _ENDPOINT,
            params={
                "engine": "google",
                "q": query,
                "num": max_results,
                "api_key": self._api_key,
                **_LOCALE_PARAMS,
            },
            timeout=15.0,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            # SerpAPI takes the key as a query parameter and httpx puts the full URL in the
            # message, which reaches the calling workflow: redact it before it leaves here.
            raise httpx.HTTPStatusError(
                str(exc).replace(self._api_key, "***"), request=exc.request, response=exc.response
            ) from None
        organic_results = response.json().get("organic_results", [])
        return [
            SearchResult(
                title=item.get("title", ""),
                url=item.get("link", ""),
                snippet=item.get("snippet", ""),
                # SerpAPI only includes "date" for some result types (e.g. news).
                published_date=_normalize_date(item.get("date")),
            )
            for item in organic_results[:max_results]
        ]
