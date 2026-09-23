"""A/B harness: variants change only prompts and the model, runs are balanced and blinded, and the
decision follows the pre-registered rule. No LLM calls: runs are faked from the golden snapshot."""

import json
import shutil
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import yaml

from wire3_gtm import ab, golden, variant
from wire3_gtm.snapshot import SNAPSHOTS_DIR

GOLDEN = SNAPSHOTS_DIR / golden.GOLDEN_SNAPSHOT


def write(path, doc):
    path.write_text(yaml.safe_dump(doc))
    return path


def v(tmp_path, name="b", **extra):
    return write(tmp_path / f"{name}.yaml", {"name": name, "hypothesis": "h", **extra})


# ============================== variants ==============================


@pytest.mark.parametrize("doc, match", [
    ({"name": "x", "hypothesis": "h", "budget": {"max_cost_usd": 9}}, "unknown keys"),
    ({"name": "x"}, "'hypothesis' is required"),
    ({"name": "x", "hypothesis": "h", "pricing_usd_per_1m": {"input": 1}}, "pricing_usd_per_1m needs"),
    ({"name": "x", "hypothesis": "h", "agents": {"analyst": {"tools": {"append": "x"}}}}, "may only change"),
    ({"name": "x", "hypothesis": "h", "tasks": {"build_strategy": {"description": "raw text"}}}, "append: text"),
], ids=["budget-change", "no-hypothesis", "partial-pricing", "tools-change", "bare-text"])
def test_a_variant_can_change_only_prompts_and_the_model(tmp_path, doc, match):
    with pytest.raises(variant.VariantError, match=match):
        variant.load(write(tmp_path / "v.yaml", doc))


def test_no_variant_means_the_committed_configuration(monkeypatch):
    from wire3_gtm.agents import DEFAULT_MODEL, _load_config, model

    monkeypatch.delenv(variant.ENV, raising=False)
    assert model() == DEFAULT_MODEL and _load_config()["analyst"]["backstory"].startswith("You are a market analyst")


def test_variant_appends_or_replaces_prompt_fields_and_sets_the_model(tmp_path, monkeypatch):
    from wire3_gtm.agents import build_agents
    from wire3_gtm.tasks import build_tasks

    path = v(tmp_path, model="gpt-5-mini",
             agents={"analyst": {"backstory": {"append": "PREFER PRIMARY SOURCES."}}},
             tasks={"build_strategy": {"expected_output": {"replace": "ONLY THIS."}}})
    monkeypatch.setenv(variant.ENV, str(path))
    agents = build_agents()
    tasks = build_tasks(agents)
    assert agents["analyst"].backstory.startswith("You are a market analyst")
    assert agents["analyst"].backstory.endswith("PREFER PRIMARY SOURCES.")
    assert tasks["build_strategy"].expected_output == "ONLY THIS."
    assert agents["strategy"].backstory == build_agents.__globals__["_load_config"]()["strategy"]["backstory"]


def test_variant_editing_a_role_that_does_not_exist_fails(tmp_path, monkeypatch):
    from wire3_gtm.agents import build_agents

    monkeypatch.setenv(variant.ENV, str(v(tmp_path, agents={"critic": {"goal": {"append": "x"}}})))
    with pytest.raises(variant.VariantError, match="not in config/agents.yaml"):
        build_agents()


def test_an_unpriced_model_refuses_to_report_zero_cost(tmp_path, monkeypatch):
    from wire3_gtm.run_log import Usage, price_for

    monkeypatch.setenv(variant.ENV, str(v(tmp_path, model="some-new-model")))
    with pytest.raises(KeyError, match="no price for model 'some-new-model'"):
        Usage(prompt_tokens=1000).cost_usd()
    priced = {"input": 1.0, "cached_input": 0.1, "output": 4.0, "verified": "2026-09-23"}
    monkeypatch.setenv(variant.ENV, str(v(tmp_path, model="some-new-model", pricing_usd_per_1m=priced)))
    assert price_for("some-new-model") == priced
    assert Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000).cost_usd() == pytest.approx(5.0)


# ============================== experiment and runs ==============================


def experiment(tmp_path, **decision):
    a, b = v(tmp_path, "control"), v(tmp_path, "challenger")
    return ab.load_experiment(write(tmp_path / "exp.yaml", {
        "name": "t", "variants": {"A": a.name, "B": b.name}, "repeats": 2,
        "decision": {"min_successful_runs": 2, "primary": "golden_errors", **decision}}))


def test_schedule_alternates_which_variant_goes_first():
    order = ab.schedule(4)
    firsts = [order[i][1] for i in range(0, len(order), 2)]
    assert firsts == ["A", "B", "A", "B"] and [lbl for _, lbl in order].count("A") == 4


def test_experiment_must_be_exactly_control_and_challenger(tmp_path):
    with pytest.raises(ValueError, match="exactly A"):
        ab.load_experiment(write(tmp_path / "e.yaml", {"name": "t", "variants": {"A": "a", "B": "b", "C": "c"},
                                                      "repeats": 1, "decision": {"primary": "golden_errors"}}))


@pytest.fixture
def fake_runs(tmp_path, monkeypatch):
    """A runs dir and a run log that ab reads, and a fake subprocess that 'runs' the pipeline by copying the
    golden artifacts into the seeded run dir and logging a run_record with the given latency and cost."""
    runs, log = tmp_path / "runs", tmp_path / "runs.jsonl"
    monkeypatch.setattr(ab, "RUNS_DIR", runs)
    monkeypatch.setattr(ab, "LOG", log)
    calls = []

    def runner(cmd, cwd, env, capture_output, text, cost=0.10, ms=300_000):
        run_id = cmd[4]
        calls.append((run_id, env[variant.ENV]))
        for n in ("03_analyst_artifact.json", "03_analyst_report.json", "04_strategy_artifact.json"):
            shutil.copy(GOLDEN / n, runs / run_id / n)
        (runs / run_id / "05_document.md").write_text(f"# Wire3 GTM Plan\nRun: {run_id} | body\n")
        with log.open("a") as f:
            f.write(json.dumps({"event_type": "run_record", "run_id": run_id, "status": "success", "duration_ms": ms,
                                "cost": {"estimated_llm_usd": cost}, "model": "gpt-5-mini",
                                "agents": [{"agent": "Analyst Agent", "llm_calls": 2, "duration_ms": ms}]}) + "\n")
        return SimpleNamespace(returncode=0, stderr="")

    return SimpleNamespace(runs=runs, log=log, runner=runner, calls=calls)


def test_a_run_is_seeded_with_the_snapshot_evidence_and_gets_its_variant(tmp_path, fake_runs):
    exp = experiment(tmp_path)
    row = ab.run_one(exp, 0, "B", fake_runs.runner, now=datetime(2026, 9, 23, tzinfo=timezone.utc))
    run_dir = fake_runs.runs / row["run_id"]
    assert (run_dir / "01_plan.json").read_text() == (GOLDEN / "01_plan.json").read_text()
    assert fake_runs.calls == [(row["run_id"], exp["variants"]["B"])]
    assert (row["variant"], row["variant_name"], row["status"], row["latency_ms"], row["cost_usd"]) == \
        ("B", "challenger", "success", 300_000, 0.10)
    # the golden artifacts pass every check except "links" (no link check was run in this fake)
    assert row["golden_by_check"] == {"sections": 0, "rq": 0, "trace": 0, "links": 1, "uncertainty": 0,
                                      "inference": 0, "format": 0}


def test_a_run_directory_is_never_reused(tmp_path, fake_runs):
    exp, now = experiment(tmp_path), datetime(2026, 9, 23, tzinfo=timezone.utc)
    ab.run_one(exp, 0, "A", fake_runs.runner, now=now)
    with pytest.raises(FileExistsError):
        ab.run_one(exp, 0, "A", fake_runs.runner, now=now)


def test_a_run_that_produced_nothing_has_no_quality_score(tmp_path, fake_runs):
    exp = experiment(tmp_path)
    row = ab.run_one(exp, 0, "A", lambda *a, **k: SimpleNamespace(returncode=1, stderr="boom"),
                     now=datetime(2026, 9, 23, tzinfo=timezone.utc))
    assert row["status"] == "no_run_record" and row["golden_errors"] is None and row["stderr_tail"] == "boom"


# ============================== blinding ==============================


def test_packets_are_shuffled_blinded_and_made_once(tmp_path, fake_runs):
    exp = experiment(tmp_path)
    ab.run_experiment(exp, fake_runs.runner, log=lambda *_: None)
    pdir = ab.make_packets(exp, fake_runs.runs, seed=7)
    key = json.loads((pdir / "key.json").read_text())["key"]
    assert sorted(key) == ["Doc-01", "Doc-02", "Doc-03", "Doc-04"]
    for label, k in key.items():
        text = (pdir / f"{label}.md").read_text()
        assert k["run_id"] not in text and label in text  # the header no longer names the run
    template = json.loads((exp["dir"] / "reviews" / "TEMPLATE.json").read_text())
    assert set(template["scores"]) == set(key) and set(template["scores"]["Doc-01"]) == set(ab.CRITERIA)
    with pytest.raises(FileExistsError):
        ab.make_packets(exp, fake_runs.runs)


def test_incomplete_reviews_are_not_counted(tmp_path, fake_runs):
    exp = experiment(tmp_path)
    ab.run_experiment(exp, fake_runs.runner, log=lambda *_: None)
    ab.make_packets(exp, fake_runs.runs, seed=1)
    full = {c: {"score": 4, "evidence": "e"} for c in ab.CRITERIA}
    partial = {**full, "usefulness": {"score": None, "evidence": ""}}
    (exp["dir"] / "reviews" / "r1.json").write_text(json.dumps({"scores": {"Doc-01": full, "Doc-02": partial}}))
    key = json.loads((exp["dir"] / "packets" / "key.json").read_text())["key"]
    assert ab.rubric_totals(exp) == {key["Doc-01"]["run_id"]: [24]}


# ============================== decision ==============================


def summary(a, b):
    base = {"name": "x", "runs": 3, "successful": 3, "failure_rate": 0.0, "golden_errors": 1, "rubric_total": 20,
            "rubric_reviewed_runs": 3, "latency_ms": 300_000, "cost_usd": 0.10, "top_tier_share": 0.6}
    return {"A": {**base, **a}, "B": {**base, **b}}


RULE = {"min_successful_runs": 3, "primary": "rubric_total", "min_gain": 2, "max_cost_increase_pct": 25,
        "max_latency_increase_pct": 25, "max_golden_error_increase": 0}


@pytest.mark.parametrize("a, b, verdict, why", [
    ({}, {"rubric_total": 23}, "B", "meets the rule"),
    ({}, {"rubric_total": 21}, "A", "below the required 2"),
    ({}, {"rubric_total": 25, "cost_usd": 0.14}, "A", "cost_usd: B is +40%"),
    ({}, {"rubric_total": 25, "latency_ms": 400_000}, "A", "latency_ms"),
    ({}, {"rubric_total": 25, "golden_errors": 2}, "A", "golden_errors"),
    ({}, {"rubric_total": 25, "failure_rate": 0.25}, "A", "failure rate"),
    ({}, {"rubric_total": 25, "successful": 2}, "no decision", "need 3 successful runs"),
    ({"rubric_total": None}, {"rubric_total": 25}, "no decision", "score the packets first"),
], ids=["b-wins", "gain-too-small", "too-expensive", "too-slow", "more-golden-errors", "fails-more",
        "too-few-runs", "unreviewed"])
def test_the_decision_follows_the_preregistered_rule(a, b, verdict, why):
    got, reasons = ab.decide(summary(a, b), RULE)
    assert got == verdict and any(why in r for r in reasons), reasons


def test_fewer_golden_errors_is_the_gain_when_that_is_the_primary_metric():
    rule = {**RULE, "primary": "golden_errors", "min_gain": 1}
    assert ab.decide(summary({"golden_errors": 3}, {"golden_errors": 1}), rule)[0] == "B"
    assert ab.decide(summary({"golden_errors": 1}, {"golden_errors": 3}), rule)[0] == "A"


def test_report_shows_both_variants_every_run_and_the_rule(tmp_path, fake_runs):
    exp = experiment(tmp_path, min_gain=1)
    ab.run_experiment(exp, fake_runs.runner, log=lambda *_: None)
    out = ab.render_report(exp)
    assert "A: control" in out and "B: challenger" in out and "Decision rule (set before the runs)" in out
    assert out.count("\n  A") + out.count("\n  B") >= 4  # every run listed, not just medians
    assert "Decision: A" in out  # identical runs: no gain, so the control is kept


# ============================== regressions from the first A/B attempt ==============================


def _golden():
    from wire3_gtm.analyst_models import AnalystArtifact
    from wire3_gtm.evidence import EvidenceSet
    from wire3_gtm.strategy_models import StrategyArtifact

    ev = EvidenceSet.model_validate_json((GOLDEN / "02_evidence_set.json").read_text()).evidence
    an = json.loads((GOLDEN / "03_analyst_artifact.json").read_text())
    st = json.loads((GOLDEN / "04_strategy_artifact.json").read_text())
    return ev, an, st, AnalystArtifact, StrategyArtifact


def test_an_invented_id_written_into_prose_fails_the_analyst_grounding_check():
    from wire3_gtm.analyst_models import check_grounding

    ev, an, _, AnalystArtifact, _ = _golden()
    valid = {e.evidence_id for e in ev}
    assert check_grounding(AnalystArtifact.model_validate(an), valid) == []
    an["assumptions_unknowns_conflicts"]["unknowns"][0]["reason_unresolved"] += " (see EV-123bb33f)"
    errors = check_grounding(AnalystArtifact.model_validate(an), valid)
    assert len(errors) == 1 and "reason_unresolved mentions EV-123bb33f" in errors[0]


def test_an_invented_id_written_into_strategy_prose_fails_its_grounding_check():
    from wire3_gtm.strategy_checks import grounding_errors

    _, an, st, AnalystArtifact, StrategyArtifact = _golden()
    analyst = AnalystArtifact.model_validate(an)
    assert grounding_errors(StrategyArtifact.model_validate(st), analyst) == []
    st["risks"][0]["statement"]["value"] += " Per EV-deadbeef."
    assert any("EV-deadbeef" in e for e in grounding_errors(StrategyArtifact.model_validate(st), analyst))


def test_pricing_plan_name_citations_reach_the_document():
    from datetime import datetime, timezone

    from wire3_gtm.docs_content import build_document, render_markdown
    from wire3_gtm.snapshot import load_snapshot

    snap = load_snapshot(GOLDEN)
    md = render_markdown(build_document(snap.strategy, snap.analyst, snap.evidence.evidence, snap.plan, "r",
                                        analyst_report=snap.analyst_report, now=datetime(2026, 1, 1, tzinfo=timezone.utc)))
    for row in snap.analyst.pricing_matrix:
        assert f"**Plan name:** {row.plan_name.value} [{', '.join(row.plan_name.evidence_ids)}]" in md


def test_a_run_whose_document_cannot_be_built_is_a_failed_run_not_a_crash(tmp_path):
    for n in ("01_plan.json", "02_evidence_set.json", "03_analyst_artifact.json", "04_strategy_artifact.json"):
        shutil.copy(GOLDEN / n, tmp_path / n)
    an = json.loads((tmp_path / "03_analyst_artifact.json").read_text())
    an["assumptions_unknowns_conflicts"]["unknowns"][0]["reason_unresolved"] += " EV-123bb33f"
    (tmp_path / "03_analyst_artifact.json").write_text(json.dumps(an))
    q = ab.quality(tmp_path)
    assert q["golden_errors"] is None and "EV-123bb33f" in q["quality_error"]
