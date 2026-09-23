"""External-boundary tests: the two search providers' HTTP APIs and validate_source's HEAD check.

Mocking strategy: patch the httpx function each module calls (httpx.get / httpx.post / httpx.head,
looked up on the module that uses it) and hand back REAL httpx.Response objects bound to a request.
That way raise_for_status(), .json() and the exception types are httpx's own, not re-implemented
in a fake, and a transport failure is simulated by raising the real httpx exception class.
Nothing here touches the network or needs an API key.
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
from mcp.server.mcpserver.exceptions import ToolError

from research import cache
from research.providers.serpapi_provider import SerpAPIProvider
from research.providers.tavily_provider import TavilyProvider
from research.tools import company_overview, validate_source

TAVILY = ("tavily", "research.providers.tavily_provider.httpx.post", "https://api.tavily.com/search", "TAVILY_API_KEY")
SERPAPI = ("serpapi", "research.providers.serpapi_provider.httpx.get", "https://serpapi.com/search", "SERPAPI_API_KEY")

TAVILY_OK = {"results": [
    {"title": "Wire 3 Pricing", "url": "https://wire3.com/pricing", "content": "1 Gig for $65/mo",
     "published_date": "2026-08-01"},
    {"title": "About Wire 3", "url": "https://wire3.com/about", "content": "Local fiber."},
]}
SERPAPI_OK = {"organic_results": [
    {"title": "Wire 3 Pricing", "link": "https://wire3.com/pricing", "snippet": "1 Gig for $65/mo",
     "date": "Aug 1, 2026"},
    {"title": "About Wire 3", "link": "https://wire3.com/about", "snippet": "Local fiber.", "date": "3 days ago"},
]}


def response(url: str, status: int = 200, *, json=None, text=None, method="POST") -> httpx.Response:
    """A real httpx.Response, bound to a request so raise_for_status() works as in production."""
    kwargs = {"json": json} if json is not None else {"text": text or ""}
    return httpx.Response(status, request=httpx.Request(method, url), **kwargs)


@pytest.fixture
def provider_env(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-tavily-key")
    monkeypatch.setenv("SERPAPI_API_KEY", "test-serpapi-key")


def reply(which, status=200, *, json=None, text=None, error=None):
    """Patch one provider's HTTP call to return a canned response, or raise `error`."""
    name, target, url, _ = which
    if error is not None:
        return patch(target, side_effect=error)
    method = "POST" if name == "tavily" else "GET"
    return patch(target, return_value=response(url, status, json=json, text=text, method=method))


# ---------- normal results ----------


def test_tavily_normal_results_are_normalized(provider_env):
    with reply(TAVILY, json=TAVILY_OK) as post:
        results = TavilyProvider().search("Wire3 pricing", max_results=5)

    assert [r.url for r in results] == ["https://wire3.com/pricing", "https://wire3.com/about"]
    assert results[0].snippet == "1 Gig for $65/mo" and results[0].published_date == "2026-08-01"
    assert results[1].published_date is None  # absent, not guessed
    sent = post.call_args.kwargs
    assert sent["json"]["api_key"] == "test-tavily-key" and sent["json"]["max_results"] == 5
    assert sent["timeout"] == 15.0


def test_serpapi_normal_results_are_normalized(provider_env):
    with reply(SERPAPI, json=SERPAPI_OK) as get:
        results = SerpAPIProvider().search("Wire3 pricing", max_results=5)

    assert [r.url for r in results] == ["https://wire3.com/pricing", "https://wire3.com/about"]
    assert results[0].published_date == "2026-08-01"  # "Aug 1, 2026" -> ISO
    assert results[1].published_date is None  # relative date is unknown, not guessed
    params = get.call_args.kwargs["params"]
    assert params["hl"] == "en" and params["gl"] == "us"  # locale pinned so dates parse


def test_provider_honours_max_results_even_if_the_api_returns_more(provider_env):
    many = {"results": [{"title": str(i), "url": f"https://x.example/{i}", "content": ""} for i in range(20)]}
    with reply(TAVILY, json=many):
        assert len(TavilyProvider().search("q", max_results=3)) == 3


# ---------- empty results ----------


@pytest.mark.parametrize("which, body", [
    (TAVILY, {"results": []}),
    (TAVILY, {}),  # key missing entirely
    (SERPAPI, {"organic_results": []}),
    (SERPAPI, {"error": "Google hasn't returned any results for this query."}),  # SerpAPI's 200 "no results"
])
def test_empty_results_are_an_empty_list_not_an_error(provider_env, which, body):
    provider = TavilyProvider() if which is TAVILY else SerpAPIProvider()
    with reply(which, json=body):
        assert provider.search("q") == []


def test_empty_search_is_returned_and_not_cached(provider_env, monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    with reply(TAVILY, json={"results": []}):
        assert company_overview("Wire3") == []
    assert cache.get("tavily", "Wire3 company overview business model", 10) is None  # retried next time


# ---------- malformed results ----------


@pytest.mark.parametrize("which", [TAVILY, SERPAPI], ids=["tavily", "serpapi"])
@pytest.mark.parametrize("body", [
    {"text": "<html>502 Bad Gateway</html>"},  # 200 with a non-JSON body (proxy error page)
    {"json": ["not", "an", "object"]},  # JSON, but a list where an object is expected
    {"json": {"results": ["just a string"], "organic_results": ["just a string"]}},  # items not objects
], ids=["html", "list-body", "string-items"])
def test_malformed_provider_payload_becomes_a_readable_tool_error(provider_env, monkeypatch, which, body):
    monkeypatch.setenv("SEARCH_PROVIDER", which[0])
    with reply(which, **body):
        with pytest.raises(ToolError, match="search provider failed"):
            company_overview("Wire3")


def test_items_missing_fields_get_empty_strings_not_a_crash(provider_env):
    with reply(TAVILY, json={"results": [{"url": "https://wire3.com"}]}):
        (r,) = TavilyProvider().search("q")
    assert (r.title, r.url, r.snippet, r.published_date) == ("", "https://wire3.com", "", None)


def test_unparseable_serpapi_date_is_none_not_a_crash(provider_env):
    odd = {"organic_results": [{"title": "t", "link": "https://x.example", "snippet": "s",
                                "date": "10‏/08‏/2026"}]}  # the bidi-marked format the locale pin avoids
    with reply(SERPAPI, json=odd):
        assert SerpAPIProvider().search("q")[0].published_date is None


# ---------- timeouts, rate limits, auth errors ----------


@pytest.mark.parametrize("which", [TAVILY, SERPAPI], ids=["tavily", "serpapi"])
@pytest.mark.parametrize("case, kwargs, message", [
    ("timeout", {"error": httpx.ReadTimeout("The read operation timed out")}, "timed out"),
    ("connect", {"error": httpx.ConnectError("[Errno 61] Connection refused")}, "Connection refused"),
    ("rate-limit", {"status": 429, "json": {"detail": "rate limit exceeded"}}, "429"),
    ("server-error", {"status": 503, "text": "unavailable"}, "503"),
    ("auth-401", {"status": 401, "json": {"detail": "invalid api key"}}, "401"),
    ("auth-403", {"status": 403, "json": {"detail": "plan exhausted"}}, "403"),
])
def test_provider_failures_surface_as_tool_errors_with_the_real_reason(provider_env, monkeypatch, which, case, kwargs, message):
    """The status code / reason must reach the caller: CrewAI's retry policy decides on it."""
    monkeypatch.setenv("SEARCH_PROVIDER", which[0])
    kwargs = dict(kwargs)  # the param dict is shared by both providers' cases
    status = kwargs.pop("status", 200)
    with reply(which, status, **kwargs):
        with pytest.raises(ToolError, match="search provider failed") as exc:
            company_overview("Wire3")
    assert message in str(exc.value)
    assert cache.get(which[0], "Wire3 company overview business model", 10) is None  # failures never cached


def test_api_key_is_sent_but_never_leaks_into_the_error_message(provider_env, monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")  # Tavily sends the key in the body, not the URL
    with reply(TAVILY, 401, json={"detail": "invalid api key"}):
        with pytest.raises(ToolError) as exc:
            company_overview("Wire3")
    assert "test-tavily-key" not in str(exc.value)


def test_serpapi_key_does_not_leak_into_the_error_message(provider_env, monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "serpapi")
    with patch(SERPAPI[1], return_value=response(
            "https://serpapi.com/search?q=x&api_key=test-serpapi-key", 401, json={}, method="GET")):
        with pytest.raises(ToolError) as exc:
            company_overview("Wire3")
    assert "test-serpapi-key" not in str(exc.value)


def test_missing_api_key_is_a_readable_tool_error(monkeypatch):
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    with pytest.raises(ToolError, match="TAVILY_API_KEY"):
        company_overview("Wire3")


# ---------- validate_source (link validation's HTTP side) ----------


def head(status=None, error=None):
    if error is not None:
        return patch("research.tools.httpx.head", side_effect=error)
    return patch("research.tools.httpx.head", return_value=response("https://wire3.com/x", status, method="HEAD"))


@pytest.mark.parametrize("status, valid", [(200, True), (301, True), (404, False), (410, False),
                                           (429, False), (401, False), (403, False), (503, False)])
def test_validate_source_reports_every_status_without_raising(status, valid):
    with head(status):
        result = validate_source("https://wire3.com/x")
    assert (result["is_valid"], result["status_code"], result["error"]) == (valid, status, None)


@pytest.mark.parametrize("error", [
    httpx.ReadTimeout("The read operation timed out"),
    httpx.ConnectTimeout("timed out"),
    httpx.ConnectError("[Errno 8] nodename nor servname provided, or not known"),
    httpx.RemoteProtocolError("Server disconnected without sending a response."),
    httpx.TooManyRedirects("Exceeded maximum allowed redirects."),
], ids=["read-timeout", "connect-timeout", "dns", "disconnect", "redirect-loop"])
def test_validate_source_network_failures_are_results_not_exceptions(error):
    with head(error=error):
        result = validate_source("https://wire3.com/x")
    assert result["is_valid"] is False and result["status_code"] is None
    assert result["error"] == str(error)  # links.classify reads this to tell DNS failure from timeout


@pytest.mark.parametrize("url", ["not a url", "ftp://wire3.com/file", "http://"])
def test_validate_source_malformed_url_is_reported_not_raised(url):
    result = validate_source(url)  # httpx rejects these before any network I/O
    assert result["is_valid"] is False and result["status_code"] is None and result["error"]
