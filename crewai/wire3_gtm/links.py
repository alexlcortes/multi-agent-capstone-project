"""Link validation for the sources a run actually cites.

Uses the MCP server's validate_source tool (a HEAD request). Only evidence that a page is gone
counts as broken: a 404/410, or a hostname that does not resolve. Everything else that is not a
success is inconclusive and is reported separately, because the brief's KPI is 0% broken links
and a wrong "broken" is as misleading as a missed one:
  blocked     403/405/429/... the site refuses bots; the page exists
  unverified  timeouts, resets, 5xx: the check could not tell (a live run's three "broken" links
              were read timeouts, and two of those pages answered a normal request with 403)
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Callable, Iterator

from crewai_tools import MCPServerAdapter

from wire3_gtm.research_tools import MCP_URL

HTTP_URL_RE = re.compile(r"^https?://\S+$", re.I)
BLOCKED_STATUSES = {401, 403, 405, 406, 429, 999}


_DNS_FAILURE = ("name or service not known", "nodename nor servname", "getaddrinfo failed", "no address associated")


def classify(result: dict) -> str:
    """ok | blocked | broken | unverified, from a validate_source result."""
    if result.get("is_valid"):
        return "ok"
    status = result.get("status_code")
    if status in BLOCKED_STATUSES:
        return "blocked"
    if status in (404, 410):
        return "broken"
    if status is None and any(m in str(result.get("error") or "").lower() for m in _DNS_FAILURE):
        return "broken"
    return "unverified"


def check_links(urls: list[str], validate: Callable[[str], dict], workers: int = 8) -> dict:
    """Validate each distinct URL. A validator crash marks that URL `unchecked`, never fails the run."""
    unique = sorted(set(urls))
    malformed = [u for u in unique if not HTTP_URL_RE.match(u or "")]
    todo = [u for u in unique if u not in malformed]

    def one(url: str) -> tuple[str, dict]:
        try:
            res = validate(url)
            return url, {"class": classify(res), "status_code": res.get("status_code"), "error": res.get("error")}
        except Exception as exc:  # noqa: BLE001
            return url, {"class": "unchecked", "status_code": None, "error": f"{type(exc).__name__}: {exc}"[:200]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = dict(pool.map(one, todo))
    by_class = {c: sorted(u for u, r in results.items() if r["class"] == c)
                for c in ("ok", "blocked", "broken", "unverified", "unchecked")}
    return {
        "urls_total": len(unique),
        "invalid_url_count": len(malformed),  # malformed: what n8n's invalid_url_count measures
        "malformed": malformed,
        "ok": len(by_class["ok"]), "blocked": len(by_class["blocked"]),
        "broken_url_count": len(by_class["broken"]), "unverified": len(by_class["unverified"]),
        "unchecked": len(by_class["unchecked"]),
        "ok_urls": by_class["ok"],
        "unchecked_urls": by_class["unchecked"],
        "unverified_urls": by_class["unverified"],
        "broken": [{"url": u, **results[u]} for u in by_class["broken"]],
        "blocked_urls": by_class["blocked"],
    }


@contextmanager
def mcp_validator(url: str = MCP_URL) -> Iterator[Callable[[str], dict]]:
    with MCPServerAdapter({"url": url, "transport": "streamable-http"}) as tools:
        tool = next(t for t in tools if t.name == "validate_source")
        yield lambda u: json.loads(tool.run(url=u))
