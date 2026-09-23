from datetime import datetime, timedelta, timezone

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research import cache
from research.tools import company_overview
from tests.test_tools import SAMPLE_RESULTS, FakeProvider, _patched


class Counting(FakeProvider):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.calls = 0

    def search(self, query, *, max_results=10):
        self.calls += 1
        return super().search(query, max_results=max_results)


def test_second_identical_search_is_served_from_cache_with_the_original_timestamp():
    p = Counting(results=SAMPLE_RESULTS)
    with _patched(p):
        first = company_overview("Wire3")
        second = company_overview("Wire3")
    assert p.calls == 1
    assert not first[0]["from_cache"] and second[0]["from_cache"]
    assert second[0]["retrieval_timestamp"] == first[0]["retrieval_timestamp"] == second[0]["cached_at"]
    assert second[0]["source_url"] == first[0]["source_url"]


def test_different_arguments_are_different_entries():
    p = Counting(results=SAMPLE_RESULTS)
    with _patched(p):
        company_overview("Wire3")
        company_overview("Spectrum")
        company_overview("Wire3", max_results=1)
    assert p.calls == 3


def test_an_expired_entry_is_searched_again():
    p = Counting(results=SAMPLE_RESULTS)
    with _patched(p):
        company_overview("Wire3")
    later = datetime.now(timezone.utc) + timedelta(hours=25)
    assert cache.get("tavily", "Wire3 company overview business model", 10, now=later) is None
    assert cache.get("tavily", "Wire3 company overview business model", 10) is not None


def test_ttl_zero_turns_the_cache_off(monkeypatch):
    monkeypatch.setenv("RESEARCH_CACHE_TTL_HOURS", "0")
    p = Counting(results=SAMPLE_RESULTS)
    with _patched(p):
        company_overview("Wire3")
        company_overview("Wire3")
    assert p.calls == 2


def test_failures_and_empty_answers_are_not_cached():
    with _patched(FakeProvider(error=RuntimeError("boom"))), pytest.raises(ToolError):
        company_overview("Wire3")
    empty = Counting(results=[])
    with _patched(empty):
        company_overview("Wire3")
        company_overview("Wire3")
    assert empty.calls == 2
    ok = Counting(results=SAMPLE_RESULTS)
    with _patched(ok):
        company_overview("Wire3")
    assert ok.calls == 1  # nothing stale was left behind by the failure or the empty answer


def test_a_corrupt_cache_file_is_ignored(tmp_path, monkeypatch):
    p = Counting(results=SAMPLE_RESULTS)
    with _patched(p):
        company_overview("Wire3")
    for f in (tmp_path / "cache").glob("*.json"):
        f.write_text("{not json")
    with _patched(p):
        company_overview("Wire3")
    assert p.calls == 2
