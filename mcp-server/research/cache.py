"""Research-result cache, shared by every caller of the MCP server (n8n and CrewAI).

One JSON file per (provider, query, max_results) under RESEARCH_CACHE_DIR
(default mcp-server/.cache/research, gitignored). A hit returns the results
exactly as first retrieved, with their ORIGINAL retrieval_timestamp: evidence
must say when the page was read, not when the cache served it. Each result also
carries from_cache/cached_at so a caller can tell a hit from a fresh search.

RESEARCH_CACHE_TTL_HOURS (default 24) bounds staleness; 0 turns the cache off.
Only successful, non-empty searches are cached: an error or an empty answer is
retried next time rather than remembered.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DIR = Path(__file__).resolve().parents[1] / ".cache" / "research"


def ttl_hours() -> float:
    return float(os.environ.get("RESEARCH_CACHE_TTL_HOURS", "24"))


def _dir() -> Path:
    return Path(os.environ.get("RESEARCH_CACHE_DIR") or DEFAULT_DIR)


def _key(provider: str, query: str, max_results: int) -> str:
    raw = json.dumps([provider, query.strip().lower(), max_results])
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _parse(ts: str) -> datetime:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def get(provider: str, query: str, max_results: int, now: datetime | None = None) -> list[dict] | None:
    """Cached results if present and younger than the TTL, else None."""
    if ttl_hours() <= 0:
        return None
    path = _dir() / f"{_key(provider, query, max_results)}.json"
    try:
        entry = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    cached_at = _parse(entry["retrieval_timestamp"])
    if (now or datetime.now(timezone.utc)) - cached_at > timedelta(hours=ttl_hours()):
        return None
    return [{**r, "from_cache": True, "cached_at": entry["retrieval_timestamp"]} for r in entry["results"]]


def put(provider: str, query: str, max_results: int, results: list[dict], retrieval_timestamp: str) -> None:
    if ttl_hours() <= 0 or not results:
        return
    d = _dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{_key(provider, query, max_results)}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"provider": provider, "query": query, "max_results": max_results,
                               "retrieval_timestamp": retrieval_timestamp, "results": results}))
    os.replace(tmp, path)  # atomic: a concurrent reader never sees half a file
