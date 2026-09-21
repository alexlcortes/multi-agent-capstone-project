import copy
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import pytest
from pydantic import ValidationError
from test_analyst_models import EV, cited, price_row, valid  # noqa: F401
from test_run_store import _seed_failed_analyst_run
from test_strategy import strategy  # noqa: F401

from wire3_gtm.analyst_checks import make_analyst_guardrail, repair_pricing
from wire3_gtm.analyst_models import AnalystArtifact, dump_contract
from wire3_gtm.evidence import EvidenceRecord

SCHEMA = json.loads((Path(__file__).parents[2] / "schemas/analyst_artifact.schema.json").read_text())
EVIDENCE = [EvidenceRecord(evidence_id=EV[0], research_question_id="RQ2", claim="c", source_title="t",
                           source_url="https://x.example/a", source_type="company",
                           retrieval_timestamp="2026-09-20T00:00:00Z", excerpt="e")]

NO_PRICE = cited(None, "inference")


def wire3_short_promo():
    """What two of three rejected drafts contained: a 1-month promo and no sourced price."""
    return price_row("Wire3", promo_duration_months=cited(1), post_promo_price_usd_per_month=NO_PRICE)


def as_output(d):
    return SimpleNamespace(raw=json.dumps(d), pydantic=None)


# --- the minimum: 3 rows, a deliberate deviation from the shared schema ------------------

def test_a_matrix_with_one_row_per_priced_competitor_is_valid(valid):
    valid["pricing_matrix"] = [price_row(n) for n in ("Spectrum", "AT&T", "T-Mobile Home Internet")]
    assert len(AnalystArtifact.model_validate(valid).pricing_matrix) == 3


def test_two_rows_is_still_too_few_and_a_missing_competitor_is_still_rejected(valid):
    valid["pricing_matrix"] = [price_row("Spectrum"), price_row("AT&T")]
    with pytest.raises(ValidationError):
        AnalystArtifact.model_validate(valid)
    valid["pricing_matrix"] = [price_row("Spectrum"), price_row("Spectrum"), price_row("AT&T")]  # 3 rows, no T-Mobile
    with pytest.raises(ValidationError, match="missing rows for"):
        AnalystArtifact.model_validate(valid)


def test_the_deviation_from_the_original_schema_is_explicit(valid):
    """3 rows satisfies our contract and n8n's behaviour, but the shared schema says minItems 4.
    If this test starts failing because the schema was changed, the deviation can be removed."""
    valid["pricing_matrix"] = [price_row(n) for n in ("Spectrum", "AT&T", "T-Mobile Home Internet")]
    doc = dump_contract(AnalystArtifact.model_validate(valid))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(doc, SCHEMA)


# --- repair A: a Wire3 row without an evidence-cited price ------------------------------

def test_a_wire3_row_without_a_sourced_price_is_dropped_and_nothing_else_is_touched(valid):
    rows = [wire3_short_promo(), price_row("Spectrum"), price_row("AT&T"), price_row("T-Mobile Home Internet")]
    valid["pricing_matrix"] = copy.deepcopy(rows)
    fixed, repairs = repair_pricing(valid)
    assert [r["competitor_name"] for r in fixed["pricing_matrix"]] == ["Spectrum", "AT&T", "T-Mobile Home Internet"]
    assert fixed["pricing_matrix"] == rows[1:]  # the surviving rows are identical
    assert [r["repair"] for r in repairs] == ["dropped_wire3_pricing_row"] and repairs[0]["row"] == 0
    assert fixed["swot"] == valid["swot"] and fixed["market_themes"] == valid["market_themes"]  # nothing outside pricing


def test_a_wire3_row_with_an_evidence_cited_price_is_kept(valid):
    valid["pricing_matrix"] = [price_row("Wire3"), price_row("Spectrum"), price_row("AT&T"), price_row("T-Mobile Home Internet")]
    fixed, repairs = repair_pricing(valid)  # price_row default: post 60.0, basis evidence
    assert repairs == [] and len(fixed["pricing_matrix"]) == 4


def test_a_wire3_row_with_only_an_inferred_price_is_dropped_even_on_a_12_month_promo(valid):
    valid["pricing_matrix"] = [price_row("Wire3", post_promo_price_usd_per_month=NO_PRICE),
                               price_row("Spectrum"), price_row("AT&T"), price_row("T-Mobile Home Internet")]
    fixed, repairs = repair_pricing(valid)
    assert repairs[0]["repair"] == "dropped_wire3_pricing_row" and len(fixed["pricing_matrix"]) == 3


def test_competitor_rows_are_never_dropped(valid):
    valid["pricing_matrix"] = [price_row("Spectrum", promo_duration_months=cited(1), post_promo_price_usd_per_month=NO_PRICE),
                               price_row("AT&T"), price_row("T-Mobile Home Internet")]
    fixed, repairs = repair_pricing(valid)
    assert repairs == [] and len(fixed["pricing_matrix"]) == 3  # left for the retry, not silently removed


# --- repair B: an inferred post-promo price, only where nulling it is clean ---------------

def test_an_inferred_price_on_a_12_month_promo_is_nulled_and_the_blend_is_unchanged(valid):
    valid["pricing_matrix"] = [price_row("Spectrum", post_promo_price_usd_per_month=cited(30.0, "inference")),
                               price_row("AT&T"), price_row("T-Mobile Home Internet")]
    fixed, repairs = repair_pricing(valid)
    assert repairs == [{"repair": "nulled_inferred_post_promo_price", "row": 0, "competitor": "Spectrum",
                        "was": 30.0, "reason": "an inferred price; irrelevant to a 12-month promo's blend"}]
    assert fixed["pricing_matrix"][0]["post_promo_price_usd_per_month"]["value"] is None
    artifact = AnalystArtifact.model_validate(fixed)  # now valid
    assert artifact.pricing_matrix[0].blended_12mo_effective_price_usd.value == 30.0  # promo 30 x 12 months


def test_an_inferred_price_is_left_alone_where_nulling_would_not_be_clean(valid):
    short = price_row("Spectrum", promo_duration_months=cited(6), post_promo_price_usd_per_month=cited(60.0, "inference"))
    no_promo = price_row("T-Mobile Home Internet", promo_status=cited("no_promo_found"),
                         promo_price_usd_per_month=cited(None, "inference"), promo_duration_months=cited(None, "inference"),
                         post_promo_price_usd_per_month=cited(50.0, "inference"))  # the trial-3 pattern
    valid["pricing_matrix"] = [short, price_row("AT&T"), no_promo]
    fixed, repairs = repair_pricing(valid)
    assert repairs == [] and fixed["pricing_matrix"] == valid["pricing_matrix"]  # untouched: these need the retry


def test_an_evidence_cited_price_is_never_touched(valid):
    valid["pricing_matrix"] = [price_row("Spectrum"), price_row("AT&T"), price_row("T-Mobile Home Internet")]
    assert repair_pricing(valid)[1] == []


def test_repairs_only_remove_rows_or_guesses_and_never_mutate_the_input(valid):
    valid["pricing_matrix"] = [wire3_short_promo(),
                               price_row("Spectrum", post_promo_price_usd_per_month=cited(30.0, "inference")),
                               price_row("AT&T"), price_row("T-Mobile Home Internet")]
    original = copy.deepcopy(valid)
    fixed, repairs = repair_pricing(valid)
    assert valid == original  # the input is a copy-on-repair
    assert len(repairs) == 2
    for before, after in zip(original["pricing_matrix"][1:], fixed["pricing_matrix"]):
        diff = {k for k in before if before[k] != after[k]}
        assert diff <= {"post_promo_price_usd_per_month"}  # the only field that may change
        if diff:
            assert after["post_promo_price_usd_per_month"]["value"] is None  # and only to "unknown"


def test_a_draft_that_is_not_a_pricing_matrix_is_left_for_the_validator(valid):
    assert repair_pricing({"pricing_matrix": "nonsense"})[1] == [] and repair_pricing({})[1] == []


# --- in the guardrail --------------------------------------------------------------------

def guard(pricing_log):
    return make_analyst_guardrail(EVIDENCE, pricing_log=pricing_log)


def test_the_guardrail_accepts_the_wire3_draft_that_used_to_cost_a_retry(valid):
    valid["pricing_matrix"] = [wire3_short_promo(), price_row("Spectrum"), price_row("AT&T"),
                               price_row("T-Mobile Home Internet")]
    log, out = [], as_output(valid)
    ok, _ = guard(log)(out)
    assert ok is True and [r["repair"] for r in log] == ["dropped_wire3_pricing_row"]
    assert isinstance(out.pydantic, AnalystArtifact)
    assert [r.competitor_name for r in out.pydantic.pricing_matrix] == ["Spectrum", "AT&T", "T-Mobile Home Internet"]


def test_the_guardrail_still_sends_back_the_case_that_cannot_be_repaired_cleanly(valid):
    no_promo = price_row("T-Mobile Home Internet", promo_status=cited("no_promo_found"),
                         promo_price_usd_per_month=cited(None, "inference"), promo_duration_months=cited(None, "inference"),
                         post_promo_price_usd_per_month=cited(50.0, "inference"))
    valid["pricing_matrix"] = [price_row("Spectrum"), price_row("AT&T"), no_promo]
    log = []
    ok, feedback = guard(log)(as_output(valid))
    assert ok is False and "never an inferred number" in feedback and log == []


def test_a_clean_draft_logs_no_repairs_and_each_attempt_starts_a_fresh_log(valid):
    valid["pricing_matrix"] = [price_row("Spectrum"), price_row("AT&T"), price_row("T-Mobile Home Internet")]
    log = [{"stale": True}]
    assert guard(log)(as_output(valid))[0] is True and log == []


# --- salvage, the run report and the document -------------------------------------------

def test_salvaging_a_saved_draft_applies_the_same_repairs(tmp_path, valid):
    from wire3_gtm.pipeline import salvage_analyst

    valid["pricing_matrix"] = [wire3_short_promo(), price_row("Spectrum"), price_row("AT&T"),
                               price_row("T-Mobile Home Internet")]
    store, _ = _seed_failed_analyst_run(tmp_path, valid, [json.dumps(valid)])
    salvage_analyst(store)
    report = json.loads(store.load_text("03_analyst_report.json"))
    assert [r["repair"] for r in report["pricing_repairs"]] == ["dropped_wire3_pricing_row"]
    assert len(json.loads(store.load_text("03_analyst_artifact.json"))["pricing_matrix"]) == 3


def test_repairs_are_listed_as_an_issue_but_do_not_degrade_the_run(tmp_path, valid, strategy):
    from test_run_store import make_steps
    from wire3_gtm.pipeline import run_pipeline
    from wire3_gtm.run_log import RunMonitor
    from wire3_gtm.run_store import RunStore

    store = RunStore("r1", root=tmp_path)
    monitor = RunMonitor(store, log_path=tmp_path / "runs.jsonl")
    steps = make_steps(valid, strategy)
    inner = steps["analyst"].fn
    steps["analyst"].fn = lambda *a: (lambda r: (setattr(r, "pricing_repairs", [{"repair": "dropped_wire3_pricing_row"}]), r)[1])(inner(*a))
    run_pipeline({"b": 1}, store=store, steps=steps, monitor=monitor)
    rc = json.loads(store.load_text("06_run_complete.json"))
    assert rc["status"] == "success"  # a code repair is information, not a failure
    assert any("pricing row(s) repaired in code" in i for i in rc["issues"])
    assert json.loads(store.load_text("03_analyst_report.json"))["pricing_repairs"] == [{"repair": "dropped_wire3_pricing_row"}]


def test_the_document_says_when_pricing_rows_were_adjusted(valid, strategy):
    from test_docs_writer import make
    from wire3_gtm.docs_content import render_markdown

    report = {"source_quality": {"cited_records": 1, "by_source_type": {"company": 1}, "top_tier_share": 1.0,
                                 "meets_80pct_target": True},
              "pricing_repairs": [{"repair": "dropped_wire3_pricing_row"}, {"repair": "nulled_inferred_post_promo_price"}]}
    md = render_markdown(make(valid, strategy, report=report))
    assert "Pricing rows adjusted automatically (Wire3 row without a sourced price dropped, or an inferred price removed): 2." in md
    clean = render_markdown(make(valid, strategy, report={**report, "pricing_repairs": []}))
    assert "Pricing rows adjusted automatically" not in clean  # silent when nothing was adjusted
