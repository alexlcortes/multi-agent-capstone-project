import pytest


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    """Each test gets an empty research cache, so a cached result never leaks between tests."""
    monkeypatch.setenv("RESEARCH_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("RESEARCH_CACHE_TTL_HOURS", "24")
