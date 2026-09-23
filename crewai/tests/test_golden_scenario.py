"""Scenario tests against the golden brief (see wire3_gtm/golden.py for what "golden" means here).

Two halves:
  * the golden run passes every check: the committed snapshot of a real run of brief.json, rendered
    into the document by the real Docs Writer code, offline;
  * every check catches its own failure: each one is shown a copy of the golden run broken in exactly
    the way it exists to catch, so a check that silently passes everything cannot survive here.
"""

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from wire3_gtm import golden
from wire3_gtm.analyst_models import dump_contract
from wire3_gtm.docs_content import _cite, build_document, render_markdown
from wire3_gtm.snapshot import SNAPSHOTS_DIR, load_snapshot

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "golden"
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)  # fixed, so the title is reproducible


@pytest.fixture(scope="module")
def run():
    """The golden run as plain dicts, plus the document built from it."""
    brief_bytes = (REPO / "brief.json").read_bytes()
    if hashlib.sha256(brief_bytes).hexdigest() != golden.GOLDEN_BRIEF_SHA256:
        pytest.fail("brief.json changed since the golden run was taken: run it again, export a new snapshot, "
                    "and update GOLDEN_BRIEF_SHA256 / GOLDEN_SNAPSHOT and the bands in golden.py")
    snap = load_snapshot(SNAPSHOTS_DIR / golden.GOLDEN_SNAPSHOT)  # refuses a hand-edited snapshot
    doc = build_document(snap.strategy, snap.analyst, snap.evidence.evidence, snap.plan, golden.GOLDEN_SNAPSHOT,
                         analyst_report=snap.analyst_report, now=NOW)
    return {
        "brief": json.loads(brief_bytes),
        "plan": snap.plan.model_dump(),
        "evidence": [e.model_dump() for e in snap.evidence.evidence],
        "analyst": dump_contract(snap.analyst),
        "strategy": dump_contract(snap.strategy),
        "report": snap.analyst_report,
        "link_check": json.loads((FIXTURES / "06_link_check.json").read_text()),
        "doc": doc,
        "markdown": render_markdown(doc),
    }


def check(name, r):
    """Run one golden check by name on a run dict."""
    return {
        "sections": lambda: golden.section_errors(r["brief"], r["doc"].blocks),
        "rq": lambda: golden.rq_coverage_errors(r["brief"], r["plan"], r["evidence"], r["analyst"], r["markdown"]),
        "trace": lambda: golden.evidence_trace_errors(r["evidence"], r["analyst"], r["strategy"], r["markdown"]),
        "links": lambda: golden.row_link_errors(r["analyst"], r["evidence"], r["markdown"], r["link_check"]),
        "uncertainty": lambda: golden.uncertainty_errors(r["analyst"], r["strategy"], r["markdown"], r["report"]),
        "inference": lambda: golden.inference_marking_errors(r["analyst"], r["strategy"], _cite),
        "format": lambda: golden.format_errors(r["doc"], r["markdown"]),
    }[name]()


def broken(run, **changes):
    r = copy.deepcopy(run)
    for k, fn in changes.items():
        out = fn(r[k])
        if out is not None:
            r[k] = out
    return r


# ============================== the golden run passes ==============================


def test_golden_run_has_every_required_section_in_the_briefs_order(run):
    assert check("sections", run) == []


def test_golden_run_has_a_cited_source_for_every_research_question(run):
    assert check("rq", run) == []


def test_golden_run_evidence_ids_flow_from_analysis_to_strategy_to_document(run):
    assert check("trace", run) == []


def test_golden_run_competitor_and_pricing_rows_have_valid_links(run):
    assert check("links", run) == []


def test_golden_run_flags_unknowns_and_unsupported_numbers(run):
    assert check("uncertainty", run) == []


def test_golden_run_marks_every_inferred_value_as_inference(run):
    assert check("inference", run) == []


def test_golden_run_is_within_the_expected_length_and_format(run):
    assert check("format", run) == []


def test_golden_counts_have_not_drifted(run):
    """The reference numbers the bands in golden.py were set from. If the snapshot is re-exported these
    change, and this test says so, instead of the bands quietly no longer fitting the reference."""
    assert (len(run["plan"]["research_questions"]), len(run["evidence"]), len(run["doc"].sources),
            len(run["markdown"].split())) == (8, 474, 61, 5150)


# ============================== each check catches its failure ==============================


def _drop_section(blocks, heading):
    i = next(i for i, b in enumerate(blocks) if b.style == "H2" and b.text.endswith(heading))
    j = next((k for k in range(i + 1, len(blocks)) if blocks[k].style == "H2"), len(blocks))
    del blocks[i:j]


def _h2(blocks, suffix):
    return next(b for b in blocks if b.style == "H2" and b.text.endswith(suffix))


@pytest.mark.parametrize("mutate, expect", [
    (lambda d: _drop_section(d.blocks, "Message pillars"), "missing section 'Message pillars'"),
    (lambda d: _drop_section(d.blocks, "Appendix B: Sources"), "missing section 'Appendix B: Sources'"),
    (lambda d: d.blocks.__setitem__(slice(None), [b for b in d.blocks if not (
        b.style != "H2" and d.blocks.index(b) > d.blocks.index(_h2(d.blocks, "Success metrics"))
        and d.blocks.index(b) < d.blocks.index(_h2(d.blocks, "Risks")))]), "is empty"),
    (lambda d: setattr(_h2(d.blocks, "Pricing matrix"), "text", "3. Pricing matrix"), "numbered 1..N"),
], ids=["dropped-section", "no-appendix", "empty-section", "misnumbered"])
def test_section_check_catches(run, mutate, expect):
    r = broken(run, doc=mutate)
    assert any(expect in e for e in check("sections", r))


def test_section_check_catches_a_brief_section_it_does_not_know(run):
    r = broken(run, brief=lambda b: b["required_output_sections"].append("Budget breakdown"))
    assert any("'Budget breakdown' has no mapping" in e for e in check("sections", r))


def test_section_check_catches_sections_out_of_the_briefs_order(run):
    def swap(doc):
        a = next(i for i, b in enumerate(doc.blocks) if b.text.endswith("Executive summary"))
        z = next(i for i, b in enumerate(doc.blocks) if b.text.endswith("Success metrics"))
        doc.blocks[a].text, doc.blocks[z].text = "1. Success metrics", "16. Executive summary"
    assert any("out of the brief's order" in e for e in check("sections", broken(run, doc=swap)))


def test_rq_check_catches_a_question_with_no_evidence(run):
    r = broken(run, evidence=lambda ev: [e for e in ev if e["research_question_id"] != "RQ5"])
    assert "RQ5 has no evidence records" in check("rq", r)


def test_rq_check_catches_a_question_the_analysis_never_cites(run):
    def uncite(ev):
        for e in ev:  # every RQ7 result moves to RQ1, and RQ7 gets one fresh result nobody cites
            if e["research_question_id"] == "RQ7":
                e["research_question_id"] = "RQ1"
        ev.append({**ev[0], "evidence_id": "EV-00000007", "research_question_id": "RQ7",
                   "source_url": "https://uncited.example", "excerpt": "never cited"})
    assert "RQ7 has evidence but no source for it is cited in the analysis" in check("rq", broken(run, evidence=uncite))


def test_rq_check_catches_a_plan_that_dropped_a_brief_question(run):
    def drop_last(p):
        del p["research_questions"][-1]
    r = broken(run, plan=drop_last)
    assert any("plan has 7 research questions, the brief 8" in e for e in check("rq", r))


def test_trace_check_catches_an_invented_evidence_id(run):
    def invent(a):
        a["competitor_comparison_table"][1]["service_type"]["evidence_ids"].append("EV-deadbeef")
    assert any("not in the evidence set" in e and "EV-deadbeef" in e for e in check("trace", broken(run, analyst=invent)))


def test_trace_check_catches_a_strategy_section_with_no_path_to_a_source(run):
    def unground(s):
        for p in s["message_pillars"]:
            for d in golden._walk(p):
                if "supporting_ids" in d:
                    d["supporting_ids"] = []
    assert "strategy section 'message_pillars' traces to no source evidence" in check("trace", broken(run, strategy=unground))


def test_trace_check_catches_a_strategy_citing_an_unknown_id(run):
    r = broken(run, strategy=lambda s: s["risks"][0]["statement"]["supporting_ids"].append("THEME-99"))
    assert any("THEME-99" in e for e in check("trace", r))


def test_trace_check_catches_appendix_b_out_of_step_with_the_body(run):
    first = run["doc"].sources[0].evidence_id
    r = broken(run, markdown=lambda md: md.replace(f"- **[{first}]**", "- **[removed]**"))
    assert any("Appendix B does not list exactly" in e and first in e for e in check("trace", r))


@pytest.mark.parametrize("mutate, expect", [
    (lambda lc: lc["broken"].append({"url": "PRICING_URL"}), "broken link"),
    (lambda lc: {"status": "unchecked", "error": "ConnectError: refused"}, "links were not checked"),
    (lambda lc: None, None),
], ids=["broken", "unchecked", "none"])
def test_link_check_catches_a_bad_link_status(run, mutate, expect):
    url = next(e["source_url"] for e in run["evidence"]
               if e["evidence_id"] in golden._ev_ids(run["analyst"]["pricing_matrix"][0]))
    r = copy.deepcopy(run)
    out = mutate(r["link_check"])
    if expect is None:
        r["link_check"], expect = None, "no link check"
    elif out is not None:
        r["link_check"] = out
    else:
        r["link_check"]["broken"][-1]["url"] = url
    assert any(expect in e for e in check("links", r))


def test_link_check_catches_a_malformed_source_url(run):
    ev_id = sorted(golden._ev_ids(run["analyst"]["competitor_comparison_table"][2]))[0]

    def mangle(ev):
        next(e for e in ev if e["evidence_id"] == ev_id)["source_url"] = "www.att.com/internet"
    assert any("malformed URL" in e for e in check("links", broken(run, evidence=mangle)))


def test_link_check_catches_a_row_with_no_source(run):
    def strip(a):
        for d in golden._walk(a["pricing_matrix"][1]):
            for k in ("evidence_ids", "derived_from_evidence_ids"):
                if k in d:
                    d[k] = []
    assert any("cites no source" in e for e in check("links", broken(run, analyst=strip)))


def test_link_check_catches_a_competitor_missing_from_the_pricing_matrix(run):
    r = broken(run, analyst=lambda a: a.__setitem__(
        "pricing_matrix", [p for p in a["pricing_matrix"] if p["competitor_name"] != "AT&T"]))
    assert any("pricing matrix has no row for ['AT&T']" in e for e in check("links", r))


def test_uncertainty_check_catches_an_unknown_without_a_follow_up(run):
    def unlink(s):
        for f in s["follow_up_research_questions"]:
            f["related_unknown_ids"] = []
    errors = check("uncertainty", broken(run, strategy=unlink))
    assert errors and all("has no follow-up research question" in e for e in errors)


def test_uncertainty_check_catches_unsupported_numbers_hidden_from_the_reader(run):
    r = broken(run, markdown=lambda md: md.replace("Numbers marked as evidence but not found in the cited text", ""))
    assert any("not surfaced in the document" in e for e in check("uncertainty", r))


def test_inference_check_catches_an_inferred_value_shown_as_sourced():
    analyst = {"x": {"value": 1.0, "evidence_ids": ["EV-00000001"], "basis": "inference"}}
    ids_only = lambda d: f"{d['value']} [{', '.join(d['evidence_ids'])}]"  # noqa: E731  the renderer before the fix
    assert golden.inference_marking_errors(analyst, {}, ids_only) != []
    assert golden.inference_marking_errors(analyst, {}, _cite) == []


@pytest.mark.parametrize("mutate, expect", [
    (lambda md: md[: len(md) // 4], "words, expected"),  # truncated
    (lambda md: md + md, "words, expected"),  # duplicated
    (lambda md: md.replace(":** 1 [inference]", ":** None [inference]", 1), "leaked"),
    (lambda md: md.replace("](http", "](ftp", 1).replace("](ftp", "", 1), "without a hyperlink"),
], ids=["truncated", "duplicated", "none-leak", "unlinked-source"])
def test_format_check_catches(run, mutate, expect):
    assert any(expect in e for e in check("format", broken(run, markdown=mutate)))


def test_format_check_catches_one_section_swallowing_the_document(run):
    filler = "\n- " + " ".join(["padding"] * 6000)
    r = broken(run, markdown=lambda md: md.replace("\n## 13. Message pillars", filler + "\n## 13. Message pillars"))
    assert any("Positioning and differentiation" in e and "of the document" in e for e in check("format", r))


def test_format_check_catches_a_malformed_title(run):
    r = broken(run, doc=lambda d: setattr(d, "title", "Wire3 GTM Plan - None - today"))
    assert any("title does not match" in e for e in check("format", r))
