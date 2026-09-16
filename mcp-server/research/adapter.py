from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SearchResult:
    """One normalized search hit, identical shape regardless of provider.

    Field names deliberately echo research_evidence.schema.json (source_title,
    source_url, excerpt, publication_date) so tools.py can build most of an
    EvidenceRecord straight from this. evidence_id, research_question_id,
    source_type, and validation_status are intentionally absent here -- the
    schema's lifecycle notes assign those at the Research Agent/orchestrator
    step, not inside the search adapter.
    """

    title: str
    url: str
    snippet: str
    published_date: str | None


class SearchProvider(ABC):
    """The one interface every one of the five MCP tools calls through.

    tools.py only ever calls provider.search(...) against this type. Swapping
    SEARCH_PROVIDER (see factory.py) never touches tools.py -- only which
    concrete class gets instantiated changes.
    """

    provider_name: str

    @abstractmethod
    def search(self, query: str, *, max_results: int = 10) -> list[SearchResult]:
        """Run one web search and return normalized results."""
        raise NotImplementedError
