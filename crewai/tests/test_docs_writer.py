import copy

import pytest
from test_analyst_models import EV, valid  # noqa: F401
from test_run_store import EVIDENCE, PLAN
from test_strategy import strategy  # noqa: F401

from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.docs_content import (
    DocsContentError, build_document, render_markdown, to_docs_requests, u16len,
)
from wire3_gtm.docs_google import flatten, simulate_docs, verify
from wire3_gtm.strategy_models import StrategyArtifact


def make(valid, strategy, evidence=None, report=None):
    return build_document(
        StrategyArtifact.model_validate(strategy), AnalystArtifact.model_validate(valid),
        evidence or EVIDENCE, PLAN, "run-test", analyst_report=report,
    )


def test_document_has_every_required_section_in_order(valid, strategy):
    plan = make(valid, strategy)
    assert plan.sections[:5] == ["Executive summary", "Research scope and evidence", "Competitor comparison",
                                 "Product and feature comparison", "Pricing matrix"]
    for needed in ("Market themes", "SWOT analysis", "Ideal customer profiles", "Value proposition",
                   "Message pillars", "Recommended channels", "Launch phases and activities", "Success metrics",
                   "Risks", "Assumptions, unknowns and conflicts", "Follow-up research questions"):
        assert needed in plan.sections
    h2 = [b.text for b in plan.blocks if b.style == "H2"]
    assert h2[:2] == ["1. Executive summary", "2. Research scope and evidence"]
    assert h2[-2:] == ["Appendix A: Analyst findings referenced", "Appendix B: Sources"]


def test_sources_are_exactly_the_evidence_ids_that_appear(valid, strategy):
    plan = make(valid, strategy)
    assert plan.expected["source_ids"] == [EV[0]]
    assert plan.expected["link_urls"] == ["https://x.example"]


def test_preflight_refuses_a_strategy_citing_an_id_not_in_the_analyst_artifact(valid, strategy):
    strategy["icps"][0]["current_provider"] = {"value": ["Spectrum"], "supporting_ids": ["THEME-9"], "basis": "evidence"}
    with pytest.raises(DocsContentError, match="THEME-9"):
        make(valid, strategy)


def test_preflight_refuses_an_analyst_citation_missing_from_the_evidence_set(valid, strategy):
    valid["competitor_comparison_table"][1]["footprint_confirmed_in_region"]["evidence_ids"] = ["EV-bbbbbbbb"]
    with pytest.raises(DocsContentError, match="EV-bbbbbbbb"):
        make(valid, strategy)


def test_preflight_refuses_an_unknown_with_no_follow_up_question(valid, strategy):
    valid["assumptions_unknowns_conflicts"]["unknowns"] = [
        {"id": "UNK-1", "research_question_id": "RQ1", "description": "d", "reason_unresolved": "r"}]
    with pytest.raises(DocsContentError, match="UNK-1"):
        make(valid, strategy)


def test_written_document_verifies_clean(valid, strategy):
    plan = make(valid, strategy)
    doc = simulate_docs(plan)
    assert verify(plan.expected, doc) == []


def test_verify_catches_each_kind_of_damage(valid, strategy):
    plan = make(valid, strategy)
    doc = simulate_docs(plan)

    gone = copy.deepcopy(doc)
    text, _, _ = flatten(gone)
    heading = plan.expected["section_headings"][2]
    for el in gone["body"]["content"]:
        for run in el["paragraph"]["elements"]:
            run["textRun"]["content"] = run["textRun"]["content"].replace(heading, "")
    assert any("Missing section heading" in e for e in verify(plan.expected, gone))

    flat = copy.deepcopy(doc)
    for el in flat["body"]["content"]:
        if heading in el["paragraph"]["elements"][0]["textRun"]["content"]:
            el["paragraph"]["paragraphStyle"]["namedStyleType"] = "NORMAL_TEXT"
    assert any("not styled as a heading" in e for e in verify(plan.expected, flat))

    nolink = copy.deepcopy(doc)
    for el in nolink["body"]["content"]:
        for run in el["paragraph"]["elements"]:
            run["textRun"]["textStyle"].pop("link", None)
    assert any("Missing hyperlink" in e for e in verify(plan.expected, nolink))

    orphan = copy.deepcopy(doc)
    orphan["body"]["content"].append({"paragraph": {"elements": [{"textRun": {"content": "see EV-deadbeef\n", "textStyle": {}}}],
                                                    "paragraphStyle": {"namedStyleType": "NORMAL_TEXT"}}})
    assert any("Orphaned" in e for e in verify(plan.expected, orphan))
    assert any("Title mismatch" in e for e in verify(plan.expected, {**doc, "title": "wrong"}))


def test_utf16_indices_put_link_and_bold_on_the_right_characters(valid, strategy):
    # an astral character (emoji) is 2 UTF-16 units but 1 Python character: naive
    # len() would shift every later index by one and misplace links and bold
    assert u16len("🚀") == 2 and len("🚀") == 1
    ev = [EVIDENCE[0].model_copy(update={"source_title": "🚀 Fiber launch 🚀"})]
    plan = make(valid, strategy, evidence=ev)
    doc = simulate_docs(plan)
    linked = [r["textRun"]["content"] for el in doc["body"]["content"] for r in el["paragraph"]["elements"]
              if r["textRun"]["textStyle"].get("link")]
    assert linked == ["🚀 Fiber launch 🚀"]  # the link covers exactly the title, nothing more or less
    bold = [r["textRun"]["content"] for el in doc["body"]["content"] for r in el["paragraph"]["elements"]
            if r["textRun"]["textStyle"].get("bold")]
    assert "[EV-aaaaaaaa] " in bold and all(b.strip() for b in bold)
    assert verify(plan.expected, doc) == []


def test_headings_land_on_heading_paragraphs(valid, strategy):
    plan = make(valid, strategy)
    _, _, paragraphs = flatten(simulate_docs(plan))
    styles = dict(paragraphs)
    for h in plan.expected["section_headings"]:
        assert styles[h] == "HEADING_2"
    assert styles["Wire3 Go-To-Market Plan"] == "TITLE"


def test_null_values_render_as_na_not_None(valid, strategy):
    md = render_markdown(make(valid, strategy))
    assert ": None" not in md and "None [" not in md  # Python's None never leaks into a value
    assert "**Contract length months:** n/a" in md  # null in the fixture; "None recorded." for empty sections is intentional


def test_evidence_quality_caveats_are_written_into_the_document(valid, strategy):
    report = {"source_quality": {"cited_records": 5, "by_source_type": {"community": 4, "company": 1},
                                 "top_tier_share": 0.2, "meets_80pct_target": False},
              "unsupported_numbers": [{"path": "x"}], "dropped_ids": [{"path": "y"}], "theme_rq_repairs": [],
              "salvaged_from": "03_analyst_attempt_4.json"}
    md = render_markdown(make(valid, strategy, report=report))
    assert "20% are primary, company or analyst sources against a target of 80% (not met)" in md
    assert "Numbers marked as evidence but not found in the cited text: 1" in md
    assert "Invented evidence ids removed from the analysis: 1" in md
    assert "recovered from a saved draft (03_analyst_attempt_4.json)" in md


def test_markdown_has_links_and_headings(valid, strategy):
    md = render_markdown(make(valid, strategy))
    assert "\n## 3. Competitor comparison" in md
    assert "[EV-aaaaaaaa]" in md and "(https://x.example)" in md


def test_requests_shape(valid, strategy):
    reqs = to_docs_requests(make(valid, strategy))
    assert list(reqs[0]) == ["insertText"] and reqs[0]["insertText"]["location"]["index"] == 1
    assert {list(r)[0] for r in reqs} == {"insertText", "updateParagraphStyle", "updateTextStyle", "createParagraphBullets"}


# --- the pipeline step, with a fake Google client ------------------------------

import json  # noqa: E402

from wire3_gtm.docs_google import simulate_requests  # noqa: E402
from wire3_gtm.pipeline import run_docs  # noqa: E402
from wire3_gtm.run_store import RunStore  # noqa: E402


class FakeGoogle:
    """Stands in for GoogleDocs. `server` is shared between instances, like Google itself,
    so a second client sees the document the first one created."""

    def __init__(self, server=None, corrupt=False):
        self.server = server if server is not None else {}
        self.calls = {"create": 0, "write": 0, "read": 0, "export": 0}
        self.corrupt = corrupt

    def create(self, title):
        self.calls["create"] += 1
        self.server["doc-123"] = {"title": title, "requests": None}
        return "doc-123"

    def write(self, document_id, requests):
        self.calls["write"] += 1
        self.server[document_id]["requests"] = requests

    def read(self, document_id):
        self.calls["read"] += 1
        d = self.server[document_id]
        doc = simulate_requests(d["title"], d["requests"])
        if self.corrupt:
            doc["title"] = "someone renamed it"
        return doc

    def export_pdf(self, document_id):
        self.calls["export"] += 1
        d = self.server[document_id]
        return tiny_pdf([d["title"]] + [p for p, _ in flatten(simulate_requests(d["title"], d["requests"]))[2]])


def tiny_pdf(lines: list[str]) -> bytes:
    """A small but real PDF with one line of text per entry, readable by pypdf."""
    esc = lambda t: t.encode("latin-1", "replace").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")  # noqa: E731
    body = b"BT /F1 8 Tf 20 800 Td 10 TL " + b"".join(b"(" + esc(t) + b") Tj T* " for t in lines) + b"ET"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 20000] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>stream\n" % len(body) + body + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


def seed_run(tmp_path, valid, strategy):
    from wire3_gtm.analyst_models import dump_contract
    from wire3_gtm.evidence import EvidenceSet

    store = RunStore("r1", root=tmp_path)
    store.save_text("01_plan.json", PLAN.model_dump_json())
    store.save_text("02_evidence_set.json", EvidenceSet(run_id="plan-1", evidence=EVIDENCE).model_dump_json())
    store.save_json("03_analyst_artifact.json", dump_contract(AnalystArtifact.model_validate(valid)))
    store.save_json("04_strategy_artifact.json", dump_contract(StrategyArtifact.model_validate(strategy)))
    return store


def test_local_backend_writes_markdown_and_never_touches_google(tmp_path, valid, strategy):
    store = seed_run(tmp_path, valid, strategy)
    result = run_docs(store, "local", client=object())  # any Google call would raise AttributeError
    assert result["status"] == "local_verified" and result["document_url"] is None
    assert "## 1. Executive summary" in store.load_text("05_document.md")
    assert store.manifest()["steps"]["docs"]["status"] == "ok"


def test_google_backend_creates_writes_verifies_and_exports(tmp_path, valid, strategy):
    store, g = seed_run(tmp_path, valid, strategy), FakeGoogle()
    result = run_docs(store, "google", client=g)
    assert g.calls == {"create": 1, "write": 1, "read": 1, "export": 1}
    assert result["status"] == "verified" and result["document_url"].endswith("/doc-123/edit")
    assert store.path("05_document.pdf").read_bytes().startswith(b"%PDF")
    assert json.loads(store.load_text("05_document.json"))["document_id"] == "doc-123"


def test_failed_post_write_check_keeps_the_document_id_and_a_rerun_verifies_it_without_creating_another(tmp_path, valid, strategy):
    store, server = seed_run(tmp_path, valid, strategy), {}
    with pytest.raises(DocsContentError, match="post-write check"):
        run_docs(store, "google", client=FakeGoogle(server, corrupt=True))
    saved = json.loads(store.load_text("05_document.json"))
    assert saved["document_id"] == "doc-123" and saved["status"] == "written"  # id not lost
    assert store.manifest()["steps"]["docs"]["status"] == "failed"

    second = FakeGoogle(server)  # same Google account, healthy this time
    result = run_docs(store, "google", client=second)
    assert second.calls == {"create": 0, "write": 0, "read": 1, "export": 1}  # verified the existing doc only
    assert result["status"] == "verified" and result["document_id"] == "doc-123"
    assert len(server) == 1  # exactly one document ever existed
    assert store.manifest()["steps"]["docs"]["status"] == "ok"


def test_no_document_is_built_from_missing_or_invalid_artifacts(tmp_path, valid, strategy):
    store = seed_run(tmp_path, valid, strategy)
    store.path("04_strategy_artifact.json").unlink()
    with pytest.raises(DocsContentError, match="04_strategy_artifact.json is missing"):
        run_docs(store, "local")
    assert not store.exists("05_document.md")


def test_values_are_formatted_for_people_not_python(valid, strategy):
    md = render_markdown(make(valid, strategy))
    assert "**Has mobile bundle available:** No [brief]" in md  # Wire3 row: False -> No
    assert "**Top advertised speed mbps down:** 1000 [" in md  # 1000.0 -> 1000
    assert "True" not in md and "False" not in md and "1000.0" not in md


def test_only_the_research_agent_is_given_the_research_tools():
    """Guide: are MCP and the search provider available to the *intended* agent?"""
    from crewai.tools import BaseTool
    from pydantic import BaseModel

    from wire3_gtm.agents import build_agents

    class A(BaseModel):
        company_name: str

    class T(BaseTool):
        name: str = "recent_news"
        description: str = "d"
        args_schema: type = A

        def _run(self, company_name: str) -> str:
            return ""

    agents = build_agents(research_tools=[T()])
    assert [a for a, ag in agents.items() if ag.tools] == ["research"]


def test_verify_catches_a_missing_table_row(valid, strategy):
    plan = make(valid, strategy)
    doc = simulate_docs(plan)
    row = next(iter(plan.expected["tables"].values()))[0]
    for el in doc["body"]["content"]:
        para = el.get("paragraph")
        if para and "".join(r["textRun"]["content"] for r in para["elements"]).rstrip("\n") == row:
            para["paragraphStyle"]["namedStyleType"] = "NORMAL_TEXT"
    assert any("missing 1 row" in e for e in verify(plan.expected, doc))


def test_expected_tables_cover_the_analyst_tables(valid, strategy):
    tables = make(valid, strategy).expected["tables"]
    names = {h.split(". ", 1)[1] for h in tables}
    assert names == {"Competitor comparison", "Product and feature comparison", "Pricing matrix",
                     "SWOT analysis", "7P market analysis"}
    assert all(tables.values())


def test_link_report_fails_broken_warns_blocked_and_unchecked():
    from wire3_gtm.docs_google import link_report
    check = {"ok_urls": ["https://a"], "broken": [{"url": "https://b"}], "blocked_urls": ["https://c"],
             "unverified_urls": [], "malformed": []}
    r = link_report(["https://a", "https://b", "https://c", "https://d"], check)
    assert r["errors"] == ["Broken link in document: https://b"]
    assert len(r["warnings"]) == 2 and r["confirmed_ok"] == 1
    assert link_report(["https://a"], None)["warnings"]


def test_broken_link_stops_the_document(tmp_path, valid, strategy):
    store = seed_run(tmp_path, valid, strategy)
    url = EVIDENCE[0].source_url
    store.save_json("06_link_check.json", {"broken": [{"url": url}], "ok_urls": []})
    with pytest.raises(DocsContentError, match="Broken link"):
        run_docs(store, "local")
    assert json.loads(store.load_text("05_verification.json"))["links"]["errors"]


def test_pdf_check(valid, strategy):
    from wire3_gtm.docs_google import verify_pdf
    plan = make(valid, strategy)
    good = tiny_pdf([plan.title] + plan.expected["section_headings"])
    assert verify_pdf(good, plan.expected) == []
    assert verify_pdf(b"%PDF-1.7 fake", plan.expected) == ["PDF export is not a complete PDF file"]
    short = tiny_pdf([plan.title] + plan.expected["section_headings"][:-1])
    assert verify_pdf(short, plan.expected) == [f"PDF is missing section heading: {plan.expected['section_headings'][-1]}"]
