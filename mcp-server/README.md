# Wire3 GTM research server (MCP)

The one research layer shared by both implementations: the n8n workflow (MCP Client Tool node) and the CrewAI
pipeline (`MCPServerAdapter`) call the same tools on the same server, so search is identical by design.
Transport is streamable HTTP at `http://127.0.0.1:8000/mcp`.

## Set up and run

Needs Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
cd mcp-server
uv sync
cp .env.example .env        # set SEARCH_PROVIDER and that provider's API key
uv run python main.py       # serves http://127.0.0.1:8000/mcp; leave it running during pipeline runs
```

| Variable | Meaning |
|---|---|
| `SEARCH_PROVIDER` | `tavily` or `serpapi`. Falls back to `tavily` if unset; any other value is an error |
| `TAVILY_API_KEY` / `SERPAPI_API_KEY` | Key for the chosen provider (only that one is needed) |
| `RESEARCH_CACHE_TTL_HOURS` | Cache lifetime, default 24; `0` turns the cache off |
| `RESEARCH_CACHE_DIR` | Optional; default `.cache/research/` (gitignored) |

Search keys live only here, never in `crewai/.env` or `n8n/.env`.

## Tools

| Tool | Arguments | Query sent to the provider |
|---|---|---|
| `company_overview` | `company_name` | `<company> company overview business model` |
| `competitor_discovery` | `company_name`, `region` | `<company> competitors in <region>` |
| `product_portfolio_mapping` | `company_name` | `<company> products plans tiers` |
| `pricing_research` | `company_name`, `region` | `<company> pricing plans promo rate <region>` |
| `recent_news` | `company_name` | `<company> news` |
| `validate_source` | `url`, `timeout` (10 s) | none: a HEAD request to the URL |
| `health_check` | none | none: reports the active provider and which keys are set (never their values) |

The five research tools take `max_results` (default 10) and return a list of results shaped like the evidence
contract (`../schemas/research_evidence.schema.json`):

```json
{"source_title": "...", "source_url": "https://...", "excerpt": "...",
 "publication_date": "2026-08-01 or null", "retrieval_timestamp": "2026-09-23T17:10:02Z", "from_cache": false}
```

Evidence ids, research-question ids and source types are assigned by the calling pipeline, not here.

`validate_source` returns `{url, is_valid, status_code, error, checked_at}`. A dead link is a normal result
(`is_valid: false`), not a tool error. How a status is classified (only 404/410/DNS failure = broken; 403/429 =
blocked; timeouts and 5xx = unverified) is decided by the caller: see `../crewai/wire3_gtm/links.py`.

**Errors** are raised as `ToolError` with the real reason (blank argument, missing key, unknown provider,
provider failure), because the MCP SDK hides the message of any other exception. The caller decides whether
to retry.

## Search provider: one adapter, two real implementations

```
tools.py ──► _run() ──► cache ──► factory.get_provider() ──► TavilyProvider | SerpAPIProvider
                                                              (research/providers/)
```

- `research/adapter.py`: the `SearchProvider` interface and the normalized `SearchResult`.
- `research/factory.py`: the only code that knows which provider classes exist; selects one by `SEARCH_PROVIDER`.
- `research/providers/`: `tavily_provider.py`, `serpapi_provider.py`.

**Why both:** the capstone brief names SerpAPI, but its free tier is about 100 searches, and one pipeline run
may use up to 40. Tavily's free tier (about 1,000 a month) was used for development and for all measured runs.
SerpAPI is implemented behind the same interface and unit-tested. Switching is one line in `.env` and needs
no code change. Full reasoning: `../SETUP_DECISIONS.md`, "Search provider".

## Cache

`research/cache.py` stores each successful, non-empty search as one JSON file keyed by (provider, query,
max_results). A hit returns the results exactly as first retrieved, with the **original**
`retrieval_timestamp`, so evidence still says when the page was read, plus `from_cache: true` and `cached_at`.
Errors and empty answers are never cached. Because n8n and CrewAI share the server, a query one of them made
in the last 24 hours costs the other nothing.

## Tests

```bash
uv run python -m pytest tests -q     # 77 tests, offline: the provider is mocked, no key or network needed
```

`tests/test_tools.py` covers the tools, the result shape and the error paths (blank input, missing key,
unknown provider, provider failure). `tests/test_cache.py` covers hits, TTL expiry and the rule that errors
and empty answers are not cached. `tests/test_boundaries.py` covers what the providers send back: normalizing both
providers' results, honouring `max_results`, empty and malformed payloads, bad dates, readable errors that never
leak an API key, and `validate_source` reporting every status and network failure without raising.

`health_check.py` is a **live** end-to-end check: it calls every tool once against the real provider
(`max_results=1`). It uses real search credits, so run it by hand, not in CI:

```bash
uv run python health_check.py
```
