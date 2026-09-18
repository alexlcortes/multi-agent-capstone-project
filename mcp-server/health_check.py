"""Live connection check for the whole MCP research server.

Unlike tests/test_tools.py (which mocks the provider so it never touches the
network or an API quota), this script calls the real, configured search
provider and every tool once each -- the "does the whole server actually
work end to end" check the guide asks for before either implementation
(n8n or CrewAI) is wired up to it. Run it manually, not as part of pytest:

    uv run python health_check.py

Uses max_results=1 per call to keep this cheap against the per-run search
budget in the project brief.
"""

from __future__ import annotations

import sys

from dotenv import load_dotenv

from research import tools

load_dotenv()

COMPANY = "Wire3"
REGION = "Ocala, FL"


def _check(label: str, fn) -> bool:
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001 -- report any failure, don't crash the pass
        print(f"FAIL  {label}: {type(exc).__name__}: {exc}")
        return False

    count = len(result) if isinstance(result, list) else 1
    print(f"PASS  {label}: {count} result(s)")
    return True


def main() -> int:
    results: list[bool] = []

    results.append(_check(
        "company_overview",
        lambda: tools.company_overview(COMPANY, max_results=1),
    ))
    results.append(_check(
        "competitor_discovery",
        lambda: tools.competitor_discovery(COMPANY, REGION, max_results=1),
    ))
    results.append(_check(
        "product_portfolio_mapping",
        lambda: tools.product_portfolio_mapping(COMPANY, max_results=1),
    ))
    results.append(_check(
        "pricing_research",
        lambda: tools.pricing_research(COMPANY, REGION, max_results=1),
    ))
    results.append(_check(
        "recent_news",
        lambda: tools.recent_news(COMPANY, max_results=1),
    ))
    results.append(_check(
        "validate_source",
        lambda: tools.validate_source("https://wire3.com"),
    ))

    passed = sum(results)
    total = len(results)
    print(f"\n{passed}/{total} tools responded correctly.")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
