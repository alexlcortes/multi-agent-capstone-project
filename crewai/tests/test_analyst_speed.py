import json
from types import SimpleNamespace

from test_analyst_models import EV, cited, valid  # noqa: F401
from test_run_store import PLAN, make_steps
from test_strategy import strategy  # noqa: F401

from wire3_gtm.analyst_checks import make_analyst_guardrail, order_by_rq, rq_checklist
from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.evidence import EvidenceRecord
from wire3_gtm.pipeline import _recording_guardrail, run_pipeline
from wire3_gtm.run_log import Budget, BudgetExceeded, RunMonitor
from wire3_gtm.run_store import RunStore
from wire3_gtm.tasks import build_tasks


def rec(eid, rq):
    return EvidenceRecord(evidence_id=eid, research_question_id=rq, claim="c", source_title="t",
                          source_url=f"https://x.example/{eid}", source_type="company",
                          retrieval_timestamp="2026-09-20T00:00:00Z", excerpt="e")


# RQ2 is cited by the fixture artifact; RQ8 has 5 records that nothing cites
EVIDENCE = [rec(EV[0], "RQ2")] + [rec(f"EV-cccccc{i:02d}", "RQ8") for i in range(5)]


def as_output(d):
    return SimpleNamespace(raw=json.dumps(d), pydantic=None)


# --- part 1: the checklist and the ordering ---------------------------------------------

def test_evidence_is_handed_over_in_research_question_order():
    mixed = [rec("EV-00000001", "RQ8"), rec("EV-00000002", "RQ2"), rec("EV-00000003", "RQ10"),
             rec("EV-00000004", "RQ2"), rec("EV-00000005", None)]
    assert [e.evidence_id for e in order_by_rq(mixed)] == [
        "EV-00000002", "EV-00000004", "EV-00000001", "EV-00000003", "EV-00000005"]  # RQ2s, RQ8, RQ10 (numeric), unassigned


def test_the_checklist_states_each_question_its_record_count_and_what_is_required():
    items = rq_checklist(PLAN, EVIDENCE)
    assert [i["research_question_id"] for i in items] == [q.id for q in PLAN.research_questions]
    rq1 = items[0]
    assert rq1["question"] == PLAN.research_questions[0].question and rq1["evidence_records"] == 0
    assert "few records" in rq1["requirement"]
    many = rq_checklist(PLAN.model_copy(update={"research_questions": [
        PLAN.research_questions[0].model_copy(update={"id": "RQ8"})]}), EVIDENCE)
    assert many[0]["evidence_records"] == 5 and many[0]["requirement"].startswith("cite at least one")


def test_the_analyst_prompt_carries_the_checklist_and_the_rule_where_the_model_will_see_it():
    from wire3_gtm.agents import build_agents

    desc = build_tasks(build_agents())["analyze_evidence"].description
    assert "{evidence_set}" in desc and "{rq_checklist}" in desc  # both are filled at kickoff
    assert "REJECTED" in desc and "tick each question off" in desc and "research-question order" in desc


# --- part 2: degrade instead of dying ---------------------------------------------------

def guard(degrade, gaps):
    return make_analyst_guardrail(EVIDENCE, degrade=degrade, gaps_log=gaps)


def test_a_grounded_valid_draft_missing_only_coverage_is_accepted_when_there_is_no_time_left(valid):
    gaps, out = [], as_output(valid)
    ok, _ = guard(lambda: True, gaps)(out)
    assert ok is True and isinstance(out.pydantic, AnalystArtifact)
    assert len(gaps) == 1 and "RQ8" in gaps[0]


def test_the_same_draft_is_re_prompted_while_there_is_time(valid):
    gaps = []
    ok, feedback = guard(lambda: False, gaps)(as_output(valid))
    assert ok is False and "RQ8" in feedback and gaps == []


def test_degrading_never_accepts_anything_but_a_coverage_gap(valid):
    # 1. a contract-rule violation is never accepted, however little time is left
    bad = json.loads(json.dumps(valid))
    bad["pricing_matrix"][0]["promo_duration_months"] = cited(6)
    bad["pricing_matrix"][0]["post_promo_price_usd_per_month"] = cited(None, "inference")
    ok, feedback = guard(lambda: True, [])(as_output(bad))
    assert ok is False and "shorter than 12 months" in feedback
    # 2. neither is a citation of evidence that does not exist (a theme must keep one id, so it cannot be repaired)
    ungrounded = json.loads(json.dumps(valid))
    ungrounded["market_themes"][0]["supporting_evidence_ids"] = ["EV-dddddddd"]
    ok, feedback = guard(lambda: True, [])(as_output(ungrounded))
    assert ok is False and "EV-dddddddd" in feedback
    # 3. and it is not JSON at all
    assert guard(lambda: True, [])(SimpleNamespace(raw="sorry", pydantic=None))[0] is False


def test_a_fully_covered_draft_is_accepted_normally_with_no_gaps(valid):
    covered = json.loads(json.dumps(valid))
    covered["market_themes"][0]["supporting_evidence_ids"] = [EV[0], "EV-cccccc00"]
    gaps = []
    assert guard(lambda: True, gaps)(as_output(covered))[0] is True and gaps == []


# --- the time estimate ------------------------------------------------------------------

def clocked(tmp_path, budget=None):
    now = [0.0]
    m = RunMonitor(RunStore("r1", root=tmp_path), budget or Budget(max_wall_clock_minutes=12),
                   log_path=tmp_path / "runs.jsonl", clock=lambda: now[0])
    return m, now


def test_can_afford_another_attempt_only_if_it_and_the_remaining_steps_fit(tmp_path):
    m, now = clocked(tmp_path)  # 12 min = 720 s, reserve 100 s
    m.on_task_started("Analyst Agent")
    now[0] = 130.0
    assert m.attempt_seconds("Analyst Agent") == 130.0  # the attempt that just ended
    assert m.can_afford_retry("Analyst Agent")  # 130 elapsed + 130 attempt + 100 reserve = 360 <= 720
    now[0] = 500.0
    assert m.attempt_seconds("Analyst Agent") == 370.0  # the next attempt was slow
    assert not m.can_afford_retry("Analyst Agent")  # 500 + 370 + 100 = 970 > 720


def test_each_attempt_is_timed_from_the_end_of_the_previous_one(tmp_path):
    m, now = clocked(tmp_path)
    m.on_task_started("Analyst Agent")
    for end, took in ((100.0, 100.0), (250.0, 150.0), (330.0, 80.0)):
        now[0] = end
        assert m.attempt_seconds("Analyst Agent") == took


def test_the_guardrail_wrapper_degrades_on_time_and_still_stops_on_a_real_breach(tmp_path, valid):
    m, now = clocked(tmp_path)
    m.on_task_started("Analyst Agent")
    gaps = []
    inner = make_analyst_guardrail(EVIDENCE, degrade=lambda: not m.can_afford_retry("Analyst Agent"), gaps_log=gaps)
    wrapped = _recording_guardrail(None, "03_analyst", inner, m, "Analyst Agent")

    now[0] = 130.0  # first attempt took 130 s: another one fits, so re-prompt
    assert wrapped(as_output(valid))[0] is False
    now[0] = 700.0  # the second took 570 s and 700 s have gone: no room for another attempt plus Strategy
    ok, _ = wrapped(as_output(valid))
    assert ok is True and gaps and m.guardrail_rejections["Analyst Agent"] == 1

    # a draft that is not acceptable, with the wall clock over the limit, still stops the run
    now[0] = 800.0
    bad = json.loads(json.dumps(valid))
    bad["pricing_matrix"][0]["promo_duration_months"] = cited(6)
    bad["pricing_matrix"][0]["post_promo_price_usd_per_month"] = cited(None, "inference")
    try:
        wrapped(as_output(bad))
        raise AssertionError("expected BudgetExceeded")
    except BudgetExceeded as e:
        assert "wall-clock budget reached" in str(e)


# --- the run is reported as degraded, and the document says why -----------------------------

def test_a_run_accepted_with_a_coverage_gap_is_degraded_and_says_so(tmp_path, valid, strategy):
    store = RunStore("r1", root=tmp_path)
    monitor = RunMonitor(store, log_path=tmp_path / "runs.jsonl")
    steps = make_steps(valid, strategy)
    inner = steps["analyst"].fn
    steps["analyst"].fn = lambda *a: (lambda r: (setattr(r, "coverage_gaps", ["RQ8 has 5 evidence records but nothing cites any"]), r)[1])(inner(*a))
    run_pipeline({"b": 1}, store=store, steps=steps, monitor=monitor)
    rc = json.loads(store.load_text("06_run_complete.json"))
    assert rc["status"] == "degraded"
    assert any("because the time budget" in i for i in rc["issues"])
    assert json.loads(store.load_text("03_analyst_report.json"))["coverage_gaps"] == ["RQ8 has 5 evidence records but nothing cites any"]


def test_the_document_states_a_coverage_gap_that_was_accepted(valid, strategy):
    from test_docs_writer import make
    from wire3_gtm.docs_content import render_markdown

    report = {"source_quality": {"cited_records": 1, "by_source_type": {"company": 1}, "top_tier_share": 1.0,
                                 "meets_80pct_target": True},
              "coverage_gaps": ["RQ8 has 5 evidence records but nothing cites any"]}
    assert "Coverage gap accepted because the time budget was reached: RQ8" in render_markdown(make(valid, strategy, report=report))


# --- coverage is judged per search result, not per duplicate id ---------------------------

from wire3_gtm.analyst_checks import coverage_errors, rq_source_depth  # noqa: E402


def result(eid, rq, url, excerpt="e"):
    return EvidenceRecord(evidence_id=eid, research_question_id=rq, claim="c", source_title="t", source_url=url,
                          source_type="company", retrieval_timestamp="2026-09-20T00:00:00Z", excerpt=excerpt)


def shared_result_evidence():
    """One page, returned by a call that served RQ2 and RQ3, so it exists as two records with two ids."""
    same = [result(EV[0], "RQ2", "https://a.example/pricing", "pricing table"),
            result("EV-bbbbbbbb", "RQ3", "https://a.example/pricing", "pricing table")]
    own = [result(f"EV-eeeeee{i:02d}", "RQ3", f"https://a.example/fees{i}", f"fee text {i}") for i in range(4)]
    return same + own  # RQ3 has 5 records, one of which is the same page the artifact cites as RQ2


def test_citing_any_copy_of_a_result_covers_every_question_that_result_served(valid):
    evidence = shared_result_evidence()
    artifact = AnalystArtifact.model_validate(valid)  # cites only EV[0], the RQ2 copy
    assert coverage_errors(artifact, evidence) == []  # RQ3 is covered: same page, different id
    assert rq_source_depth(artifact, evidence) == {"RQ2": 1, "RQ3": 1}


def test_a_question_whose_results_are_all_unused_still_fails(valid):
    evidence = [result(EV[0], "RQ2", "https://a.example/pricing")] + [
        result(f"EV-ffffff{i:02d}", "RQ8", f"https://a.example/channels{i}", f"channel text {i}") for i in range(5)]
    errors = coverage_errors(AnalystArtifact.model_validate(valid), evidence)
    assert len(errors) == 1 and "RQ8" in errors[0] and "none of the search results returned for it" in errors[0]


def test_listing_a_question_under_unknowns_does_not_excuse_unused_evidence(valid):
    evidence = [result(EV[0], "RQ2", "https://a.example/pricing")] + [
        result(f"EV-ffffff{i:02d}", "RQ8", f"https://a.example/channels{i}", f"channel text {i}") for i in range(5)]
    valid["assumptions_unknowns_conflicts"]["unknowns"] = [
        {"id": "UNK-1", "research_question_id": "RQ8", "description": "d", "reason_unresolved": "r"}]
    assert coverage_errors(AnalystArtifact.model_validate(valid), evidence)  # still an error


def test_depth_counts_distinct_results_not_ids(valid):
    evidence = shared_result_evidence()
    valid["market_themes"][0]["supporting_evidence_ids"] = [EV[0], "EV-bbbbbbbb"]  # two ids, ONE page
    valid["market_themes"][1]["supporting_evidence_ids"] = ["EV-eeeeee00"]  # a second page, RQ3 only
    depth = rq_source_depth(AnalystArtifact.model_validate(valid), evidence)
    assert depth == {"RQ2": 1, "RQ3": 2}


def test_the_document_reports_how_thin_the_thinnest_question_is(valid, strategy):
    from test_docs_writer import make
    from wire3_gtm.docs_content import render_markdown

    report = {"source_quality": {"cited_records": 3, "by_source_type": {"company": 3}, "top_tier_share": 1.0,
                                 "meets_80pct_target": True}, "rq_source_depth": {"RQ1": 9, "RQ3": 5, "RQ8": 12}}
    assert "at least 5 distinct cited source(s); the thinnest is RQ3" in render_markdown(make(valid, strategy, report=report))
