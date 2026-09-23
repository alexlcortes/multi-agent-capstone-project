"""External-boundary tests for the CrewAI side, each boundary mocked at the seam the code already owns:

  MCP tool calls   a fake BaseTool as the `inner` tool that _recording wraps, and a fake
                   MCPServerAdapter (context manager yielding fake tools) for mcp_preflight /
                   mcp_validator. Errors are raised the way the adapter surfaces them: a generic
                   exception carrying the server's ToolError text.
  LLM responses    two layers. Content (normal / empty / malformed replies) goes straight into the
                   guardrails as a stand-in TaskOutput (raw text, pydantic=None), which is exactly what
                   CrewAI hands them. Transport failures (timeout / 429 / 401) replace pipeline.Crew with
                   a fake whose kickoff raises the OpenAI SDK's own exception classes (the model is
                   gpt-5-mini), so no agent loop, network or key is involved.
  Google Docs      googleapiclient.discovery.build patched to return a MagicMock service whose
                   .execute() returns canned dicts or raises googleapiclient's real HttpError; the
                   credentials() path patches the token location and google.oauth2's loader.
  Link validation  check_links takes the validator as a callable, so a plain function stands in for
                   the MCP validate_source tool and returns its documented result shapes.
"""

import json
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai
import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response as Httplib2Response
from test_analyst_models import valid  # noqa: F401
from test_plan_enforcement import fake_tool
from test_run_log import GOOD, ToolError
from test_run_store import EVIDENCE
from test_strategy import strategy  # noqa: F401

from wire3_gtm import docs_google, links, pipeline, research_tools
from wire3_gtm.analyst_checks import make_analyst_guardrail
from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.evidence import EvidenceCollector
from wire3_gtm.research_tools import RESEARCH_TOOLS, McpUnavailable, RetryPolicy, _recording, is_transient, mcp_preflight
from wire3_gtm.strategy_checks import make_strategy_guardrail

# ============================== MCP tool calls ==============================


def recording(script, **policy):
    collector, log, sleeps = EvidenceCollector(), [], []
    tool = _recording(fake_tool("company_overview", script, log), collector, None,
                      RetryPolicy(sleep=sleeps.append, **policy))
    return tool, collector, log, sleeps


def test_mcp_normal_result_becomes_evidence():
    tool, collector, log, sleeps = recording([GOOD])
    assert tool.run(company_name="Wire3") == GOOD
    (rec,) = collector.calls
    assert rec.status == "ok" and rec.results[0]["source_url"] == "https://x.example"
    assert len(log) == 1 and sleeps == []


def test_mcp_multi_item_result_in_the_adapters_python_list_shape_is_parsed():
    raw = str([GOOD, GOOD.replace("x.example", "y.example")])  # the adapter's "['{...}', '{...}']" shape
    tool, collector, _, _ = recording([raw])
    tool.run(company_name="Wire3")
    assert [r["source_url"] for r in collector.calls[0].results] == ["https://x.example", "https://y.example"]


@pytest.mark.parametrize("raw", ["[]", "{}", '{"source_title": "no url here"}'])
def test_mcp_empty_result_is_recorded_as_empty_not_failed_and_not_retried(raw):
    tool, collector, log, sleeps = recording([raw])
    assert tool.run(company_name="Wire3") == raw
    assert collector.calls[0].status == "empty_result" and len(log) == 1 and sleeps == []


@pytest.mark.parametrize("raw", ["<html>502 Bad Gateway</html>", '{"source_url": "https://x', ""])
def test_mcp_malformed_result_is_recorded_as_failed_without_crashing_the_agent(raw):
    tool, collector, _, _ = recording([raw])
    tool.run(company_name="Wire3")  # the agent still gets text back
    (rec,) = collector.calls
    assert rec.status == "failed" and rec.error.startswith("unparseable output") and rec.results == []


@pytest.mark.parametrize("error", [
    TimeoutError("timed out"),
    ToolError("search provider failed: The read operation timed out"),
    ToolError("search provider failed: Client error '429 Too Many Requests' for url 'https://api.tavily.com/search'"),
    ToolError("search provider failed: Server error '503 Service Unavailable'"),
    ConnectionError("[Errno 61] Connection refused"),
], ids=["timeout", "provider-timeout", "rate-limit", "503", "mcp-down"])
def test_mcp_transient_failures_are_retried_with_backoff_then_succeed(error):
    tool, collector, log, sleeps = recording([error, error, GOOD])
    assert tool.run(company_name="Wire3") == GOOD
    (rec,) = collector.calls  # one record for three attempts: no duplicate evidence
    assert rec.status == "ok" and rec.attempts == 3 and sleeps == [2.0, 4.0]


def test_mcp_persistent_rate_limit_gives_up_after_the_bounded_attempts():
    error = ToolError("search provider failed: Client error '429 Too Many Requests'")
    tool, collector, log, sleeps = recording([error])
    out = tool.run(company_name="Wire3")
    assert out.startswith("TOOL ERROR") and "429" in out
    (rec,) = collector.calls
    assert rec.status == "failed" and rec.attempts == 3 and len(rec.attempt_errors) == 3


def test_mcp_bad_argument_is_not_retried():
    tool, collector, log, sleeps = recording([ToolError("company_name must not be empty")])
    tool.run(company_name="Wire3")
    assert len(log) == 1 and sleeps == [] and collector.calls[0].status == "failed"


@pytest.mark.parametrize("status", ["401 Unauthorized", "403 Forbidden"])
def test_mcp_auth_error_is_not_retried(status):
    assert not is_transient(ToolError(f"search provider failed: Client error '{status}' for url 'https://api.tavily.com/search'"))


# --- the MCP adapter itself (preflight / validator) ---


class McpTool:
    """What MCPServerAdapter yields, as far as preflight and the validator use it: a name and run()."""

    def __init__(self, name, returns=GOOD):
        self.name, self.returns = name, returns

    def run(self, **kwargs):
        return self.returns


def mcp_tool(name, returns=GOOD):
    return McpTool(name, returns)


@contextmanager
def fake_adapter(tools=None, error=None):
    """Stand-in for crewai_tools.MCPServerAdapter: connecting raises `error`, or yields `tools`."""
    def adapter(params):
        @contextmanager
        def cm():
            if error is not None:
                raise error
            yield tools
        return cm()
    with patch.object(research_tools, "MCPServerAdapter", adapter), patch.object(links, "MCPServerAdapter", adapter):
        yield


def health(provider="tavily", configured=True):
    return json.dumps({"status": "ok", "active_provider": provider, f"{provider}_configured": configured})


def test_preflight_normal():
    tools = [mcp_tool(n) for n in RESEARCH_TOOLS] + [mcp_tool("health_check", health())]
    with fake_adapter(tools):
        info = mcp_preflight()
    assert info["active_provider"] == "tavily" and info["tools"] == sorted(RESEARCH_TOOLS)


@pytest.mark.parametrize("error", [httpx.ConnectError("Connection refused"), httpx.ReadTimeout("timed out"),
                                   TimeoutError()], ids=["refused", "read-timeout", "timeout"])
def test_preflight_unreachable_server_fails_fast_with_the_start_command(error):
    with fake_adapter(error=error), pytest.raises(McpUnavailable, match="uv run python main.py"):
        mcp_preflight()


def test_preflight_server_with_no_tools_is_unavailable():
    with fake_adapter([]), pytest.raises(McpUnavailable, match="missing tools"):
        mcp_preflight()


def test_preflight_provider_without_an_api_key_is_an_auth_failure_before_any_search():
    tools = [mcp_tool(n) for n in RESEARCH_TOOLS] + [mcp_tool("health_check", health(configured=False))]
    with fake_adapter(tools), pytest.raises(McpUnavailable, match="no API key"):
        mcp_preflight()


def test_preflight_malformed_health_check_is_unavailable_not_a_crash():
    tools = [mcp_tool(n) for n in RESEARCH_TOOLS] + [mcp_tool("health_check", "<html>oops</html>")]
    with fake_adapter(tools), pytest.raises(McpUnavailable, match="JSONDecodeError"):
        mcp_preflight()


# ============================== link validation ==============================


def test_mcp_validator_parses_the_tools_json_result():
    result = {"url": "https://a.example", "is_valid": True, "status_code": 200, "error": None}
    with fake_adapter([mcp_tool("validate_source", json.dumps(result))]):
        with links.mcp_validator() as validate:
            assert validate("https://a.example") == result


def validator(results):
    def validate(url):
        r = results[url]
        if isinstance(r, Exception):
            raise r
        return r
    return validate


def test_link_check_empty_url_list():
    out = links.check_links([], validator({}))
    assert out["urls_total"] == 0 and out["broken"] == [] and out["ok"] == 0


@pytest.mark.parametrize("result, cls", [
    ({"is_valid": True, "status_code": 200, "error": None}, "ok"),
    ({"is_valid": False, "status_code": None, "error": "The read operation timed out"}, "unverified"),
    ({"is_valid": False, "status_code": 429, "error": None}, "blocked"),  # rate limited: page exists
    ({"is_valid": False, "status_code": 401, "error": None}, "blocked"),  # auth wall: page exists
    ({"is_valid": False, "status_code": 404, "error": None}, "broken"),
    ({}, "unverified"),  # malformed: no fields at all
    ({"is_valid": "yes"}, "ok"),  # malformed type: truthy is_valid is taken at its word
], ids=["ok", "timeout", "429", "401", "404", "empty-dict", "bad-type"])
def test_link_check_classifies_each_validator_answer(result, cls):
    out = links.check_links(["https://a.example"], validator({"https://a.example": result}))
    bucket = {"ok": out["ok_urls"], "unverified": out["unverified_urls"], "blocked": out["blocked_urls"],
              "broken": [b["url"] for b in out["broken"]]}[cls]
    assert bucket == ["https://a.example"]


@pytest.mark.parametrize("error", [TimeoutError("MCP call timed out"), ToolError("url must not be empty"),
                                   json.JSONDecodeError("Expecting value", "<html>", 0)],
                         ids=["timeout", "tool-error", "malformed-json"])
def test_link_check_validator_failure_marks_url_unchecked_and_run_continues(error):
    out = links.check_links(["https://a.example", "https://b.example"], validator({
        "https://a.example": error, "https://b.example": {"is_valid": True, "status_code": 200}}))
    assert out["unchecked_urls"] == ["https://a.example"] and out["ok_urls"] == ["https://b.example"]


def test_link_check_step_survives_the_mcp_server_being_down(tmp_path, valid):
    from wire3_gtm.run_store import RunStore

    store = RunStore("r1", root=tmp_path)
    ctx = SimpleNamespace(store=store, fns={"links": MagicMock(side_effect=httpx.ConnectError("refused"))},
                          monitor=MagicMock())
    with patch.object(pipeline, "_load", return_value=None):
        report = pipeline.step_links(ctx, AnalystArtifact.model_validate(valid), EVIDENCE)
    assert report["status"] == "unchecked" and "refused" in report["error"]


# ============================== LLM responses ==============================


def reply(text):
    """What CrewAI hands a guardrail: the raw completion, pydantic None until the guardrail parses it."""
    return SimpleNamespace(raw=text, pydantic=None)


def test_llm_normal_reply_passes_the_strategy_guardrail(valid, strategy):
    ok, out = make_strategy_guardrail(AnalystArtifact.model_validate(valid))(reply(json.dumps(strategy)))
    assert ok is True and out.pydantic is not None


@pytest.mark.parametrize("text", ["", "   ", "null", "{}"], ids=["empty", "whitespace", "null", "empty-object"])
def test_llm_empty_reply_is_rejected_with_feedback(valid, text):
    ok, feedback = make_strategy_guardrail(AnalystArtifact.model_validate(valid))(reply(text))
    assert ok is False and "not a valid" in feedback


@pytest.mark.parametrize("text", ["", "   ", "{}", "Sorry, I can't help with that."])
def test_llm_empty_reply_is_rejected_by_the_analyst_guardrail(text):
    ok, feedback = make_analyst_guardrail(EVIDENCE)(reply(text))
    assert ok is False and "not a valid" in feedback


@pytest.mark.parametrize("text", ["null", "[]"])
def test_llm_non_object_json_is_rejected_by_the_analyst_guardrail(text):
    ok, feedback = make_analyst_guardrail(EVIDENCE)(reply(text))
    assert ok is False and "not a valid" in feedback


@pytest.mark.parametrize("mangle", [
    lambda s: "Here is the strategy:\n```json\n" + json.dumps(s) + "\n```",  # prose + fenced JSON
    lambda s: json.dumps(s)[: len(json.dumps(s)) // 2],  # truncated at the token cap
    lambda s: json.dumps({**s, "icps": "a string where a list belongs"}),  # wrong type
    lambda s: json.dumps({k: v for k, v in s.items() if k != "risks"}),  # required field missing
    lambda s: json.dumps([s]),  # wrapped in a list
], ids=["prose-wrapped", "truncated", "wrong-type", "missing-field", "list-wrapped"])
def test_llm_malformed_reply_is_rejected_with_feedback_the_model_can_act_on(valid, strategy, mangle):
    ok, feedback = make_strategy_guardrail(AnalystArtifact.model_validate(valid))(reply(mangle(strategy)))
    assert ok is False and "StrategyArtifact" in feedback and "Fix" in feedback


def openai_error(cls, status):
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    if cls is openai.APITimeoutError:
        return cls(request=req)
    return cls(f"Error code: {status}", response=httpx.Response(status, request=req), body=None)


class FakeCrew:
    """Replaces pipeline.Crew. kickoff raises `error`, or sets each task's output to `output`."""
    error = None
    output = None

    def __init__(self, agents, tasks, process):
        self.tasks = tasks

    def kickoff(self, inputs):
        if FakeCrew.error is not None:
            raise FakeCrew.error
        for t in self.tasks:
            t.output = FakeCrew.output


@pytest.fixture
def fake_crew(monkeypatch):
    monkeypatch.setattr(pipeline, "Crew", FakeCrew)
    FakeCrew.error, FakeCrew.output = None, None
    yield FakeCrew


@pytest.mark.parametrize("error", [
    openai_error(openai.APITimeoutError, None),
    openai_error(openai.RateLimitError, 429),
    openai_error(openai.AuthenticationError, 401),
    openai_error(openai.InternalServerError, 500),
], ids=["timeout", "rate-limit", "auth", "500"])
def test_llm_transport_errors_propagate_unchanged_and_nothing_is_saved(fake_crew, tmp_path, valid, error):
    """The step must not swallow an LLM failure or save a half artifact; the run log reports the real class."""
    from wire3_gtm.run_store import RunStore

    store = RunStore("r1", root=tmp_path)
    fake_crew.error = error
    with pytest.raises(type(error)):
        pipeline.run_strategy(AnalystArtifact.model_validate(valid), store)
    assert not store.exists("04_strategy_artifact.json")


def test_llm_reply_that_never_validated_is_a_clear_error(fake_crew, valid):
    fake_crew.output = SimpleNamespace(pydantic=None, raw="I could not complete this.")
    with pytest.raises(RuntimeError, match="did not return a valid StrategyArtifact"):
        pipeline.run_strategy(AnalystArtifact.model_validate(valid))


def test_llm_normal_reply_becomes_the_strategy_result(fake_crew, valid, strategy):
    from wire3_gtm.strategy_models import StrategyArtifact

    fake_crew.output = SimpleNamespace(pydantic=StrategyArtifact.model_validate(strategy), raw=json.dumps(strategy))
    result = pipeline.run_strategy(AnalystArtifact.model_validate(valid))
    assert result.artifact.model_dump() == StrategyArtifact.model_validate(strategy).model_dump()


# ============================== Google Docs service ==============================


def http_error(status, reason="error"):
    return HttpError(Httplib2Response({"status": str(status)}), json.dumps({"error": {"message": reason}}).encode())


@pytest.fixture
def google():
    """GoogleDocs over MagicMock services; `svc.docs` / `svc.drive` are what build() returned."""
    docs, drive = MagicMock(), MagicMock()
    with patch("googleapiclient.discovery.build", side_effect=lambda api, *a, **k: docs if api == "docs" else drive):
        client = docs_google.GoogleDocs(creds=object())
    return SimpleNamespace(client=client, docs=docs, drive=drive)


def test_docs_normal_create_read_export(google):
    google.docs.documents().create().execute.return_value = {"documentId": "doc-1", "title": "t"}
    google.docs.documents().get().execute.return_value = {"title": "t", "body": {"content": []}}
    google.drive.files().export().execute.return_value = b"%PDF-1.4 ..."
    assert google.client.create("t") == "doc-1"
    assert google.client.read("doc-1")["title"] == "t"
    assert google.client.export_pdf("doc-1").startswith(b"%PDF")


def test_docs_idempotent_calls_retry_but_batch_update_never_does(google):
    google.docs.documents().create().execute.return_value = {"documentId": "d"}
    google.client.create("t")
    google.client.write("d", [{"insertText": {}}])
    google.client.read("d")
    assert google.docs.documents().create().execute.call_args.kwargs == {"num_retries": 3}
    assert google.docs.documents().get().execute.call_args.kwargs == {"num_retries": 3}
    assert google.docs.documents().batchUpdate().execute.call_args.kwargs == {"num_retries": 0}  # would duplicate text


def test_docs_empty_document_read_back_fails_verification_not_a_crash():
    expected = {"title": "t", "section_headings": ["1. Executive summary"], "source_ids": ["EV-1"],
                "link_urls": ["https://a.example"], "allowed_evidence_ids": [], "tables": {}}
    errors = docs_google.verify(expected, {"title": "t"})  # no body at all
    assert any("Missing section heading" in e for e in errors) and any("Missing hyperlink" in e for e in errors)


def test_docs_malformed_create_response_raises_instead_of_returning_no_id(google):
    google.docs.documents().create().execute.return_value = {"title": "t"}  # no documentId
    with pytest.raises(KeyError):
        google.client.create("t")


@pytest.mark.parametrize("pdf", [b"", b"<html>Sign in</html>", b"%PDF-1.4 truncated"], ids=["empty", "html", "truncated"])
def test_docs_malformed_pdf_export_is_reported(pdf):
    assert docs_google.verify_pdf(pdf, {"title": "t - r1", "section_headings": []}) == ["PDF export is not a complete PDF file"]


@pytest.mark.parametrize("method, call", [
    ("create", lambda g: g.client.create("t")),
    ("write", lambda g: g.client.write("d", [])),
    ("read", lambda g: g.client.read("d")),
    ("export", lambda g: g.client.export_pdf("d")),
])
@pytest.mark.parametrize("error", [TimeoutError("timed out"), http_error(429, "Quota exceeded"),
                                   http_error(401, "Invalid Credentials"), http_error(403, "insufficient scopes")],
                         ids=["timeout", "429", "401", "403"])
def test_docs_api_errors_propagate_from_every_call(google, method, call, error):
    target = {"create": google.docs.documents().create(), "write": google.docs.documents().batchUpdate(),
              "read": google.docs.documents().get(), "export": google.drive.files().export()}[method]
    target.execute.side_effect = error
    with pytest.raises(type(error)):
        call(google)


class FailingGoogle:
    """A Docs client that fails at one call, to check what run_docs leaves behind."""

    def __init__(self, fail_at, error):
        self.fail_at, self.error, self.created = fail_at, error, 0

    def _maybe(self, name):
        if name == self.fail_at:
            raise self.error

    def create(self, title):
        self._maybe("create")
        self.created += 1
        return "doc-9"

    def write(self, document_id, requests):
        self._maybe("write")

    def read(self, document_id):
        self._maybe("read")
        raise AssertionError("unreachable in these tests")


@pytest.mark.parametrize("error", [TimeoutError("timed out"), http_error(429, "Quota exceeded"),
                                   http_error(401, "Invalid Credentials")], ids=["timeout", "429", "401"])
def test_docs_write_failure_keeps_the_created_document_id(tmp_path, valid, strategy, error):
    """batchUpdate is not retried; the id is saved first, so the user can inspect or delete the half-written doc."""
    from test_docs_writer import seed_run

    store = seed_run(tmp_path, valid, strategy)
    with pytest.raises(type(error)):
        pipeline.run_docs(store, "google", client=FailingGoogle("write", error))
    saved = json.loads(store.load_text("05_document.json"))
    assert saved["document_id"] == "doc-9" and saved["status"] == "created"
    assert store.manifest()["steps"]["docs"]["status"] == "failed"


@pytest.mark.parametrize("error", [TimeoutError("timed out"), http_error(429), http_error(401)],
                         ids=["timeout", "429", "401"])
def test_docs_create_failure_leaves_no_document_record(tmp_path, valid, strategy, error):
    from test_docs_writer import seed_run

    store = seed_run(tmp_path, valid, strategy)
    with pytest.raises(type(error)):
        pipeline.run_docs(store, "google", client=FailingGoogle("create", error))
    assert not store.exists("05_document.json")
    assert store.exists("05_document.md")  # the local Markdown is still there


def test_docs_missing_token_is_an_auth_error_with_the_fix(tmp_path, monkeypatch):
    monkeypatch.setattr(docs_google, "TOKEN", tmp_path / "token.json")
    with pytest.raises(docs_google.DocsAuthError, match="google-auth"):
        docs_google.credentials()


def test_docs_revoked_token_is_an_auth_error_and_the_token_is_not_overwritten(tmp_path, monkeypatch):
    from google.auth.exceptions import RefreshError

    token = tmp_path / "token.json"
    token.write_text('{"sentinel": true}')
    monkeypatch.setattr(docs_google, "TOKEN", token)
    creds = MagicMock(valid=False)
    creds.refresh.side_effect = RefreshError("invalid_grant: Token has been expired or revoked.")
    with patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=creds):
        with pytest.raises(docs_google.DocsAuthError, match="expired or revoked"):
            docs_google.credentials()
    assert token.read_text() == '{"sentinel": true}'


def test_docs_expired_token_is_refreshed_and_saved(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text("{}")
    monkeypatch.setattr(docs_google, "TOKEN", token)
    creds = MagicMock(valid=False)
    creds.to_json.return_value = '{"refreshed": true}'
    with patch("google.oauth2.credentials.Credentials.from_authorized_user_file", return_value=creds):
        assert docs_google.credentials() is creds
    creds.refresh.assert_called_once()
    assert json.loads(token.read_text()) == {"refreshed": True}
