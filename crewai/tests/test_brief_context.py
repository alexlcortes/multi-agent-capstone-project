"""Running a brief other than Ocala: competitor names, row counts, prompts and run ids come from the brief."""

import json
import re

import pytest
import yaml
from pydantic import ValidationError
from test_analyst_models import comp_row, feature_row, price_row, valid  # noqa: F401
from test_strategy import strategy  # noqa: F401

from wire3_gtm import brief_context
from wire3_gtm.agents import CONFIG_DIR
from wire3_gtm.analyst_models import AnalystArtifact
from wire3_gtm.brief_context import OCALA, fill_config, from_brief, load, using
from wire3_gtm.run_log import Budget
from wire3_gtm.run_store import new_run_id
from wire3_gtm.strategy_models import StrategyArtifact
from wire3_gtm.tasks import output_models

LAKE = from_brief(load("lake-county"))


def lake_artifact(valid):
    names = LAKE.all_names
    valid["competitor_comparison_table"] = [comp_row(n) for n in names]
    valid["product_feature_comparison"] = [feature_row(n) for n in names]
    valid["pricing_matrix"] = [price_row(n) for n in LAKE.competitors]
    return valid


def test_the_root_brief_is_ocala_and_ocala_is_the_default():
    assert from_brief(load()) is OCALA and brief_context.active() is OCALA
    assert OCALA.all_names == ("Wire3", "Spectrum", "AT&T", "T-Mobile Home Internet")


def test_lake_county_brief():
    assert LAKE.key == "wire3-lake-county" and LAKE.run_prefix == "LAKE"
    assert LAKE.all_names == ("Wire3", "Xfinity", "CenturyLink", "Quantum Fiber", "Spectrum",
                              "T-Mobile Home Internet", "Verizon Home Internet")
    brief = load("lake-county")
    assert len(brief["research_questions"]) == 11 and len(brief["required_output_sections"]) == 15
    b = Budget.from_brief(brief)
    assert (b.max_search_calls, b.max_wall_clock_minutes, b.max_llm_calls_per_role, b.max_cost_usd) == (60, 20, 8, 2.5)


def test_unknown_brief_name_lists_the_available_ones():
    with pytest.raises(FileNotFoundError, match="lake-county"):
        load("no-such-brief")


def test_run_ids_carry_the_brief_prefix_and_ocala_is_unchanged():
    assert re.fullmatch(r"LAKE-\d{8}-\d{6}", new_run_id("LAKE"))
    assert re.fullmatch(r"run-\d{8}-\d{6}", new_run_id(OCALA.run_prefix)) and new_run_id().startswith("run-")


def test_using_restores_the_previous_brief():
    with using(LAKE):
        assert brief_context.active() is LAKE
    assert brief_context.active() is OCALA


def test_a_saved_run_is_reopened_in_its_own_brief(tmp_path):
    (tmp_path / "00_brief.json").write_text(json.dumps(load("lake-county")))
    assert brief_context.for_run(tmp_path) == LAKE
    assert brief_context.for_run(tmp_path / "missing") is OCALA


def test_lake_artifact_validates_under_its_brief_and_not_under_ocala(valid):
    art = lake_artifact(valid)
    with using(LAKE):
        AnalystArtifact.model_validate(art)
    with pytest.raises(ValidationError):
        AnalystArtifact.model_validate(art)  # Ocala is active again: Xfinity is not one of its names


def test_ocala_names_are_rejected_under_lake(valid):
    with using(LAKE), pytest.raises(ValidationError, match="competitor_name must be one of"):
        AnalystArtifact.model_validate(valid)  # the Ocala fixture: AT&T is not a Lake County competitor


def test_lake_needs_one_row_per_competitor(valid):
    art = lake_artifact(valid)
    art["competitor_comparison_table"] = art["competitor_comparison_table"][:6]
    with using(LAKE), pytest.raises(ValidationError, match="exactly one row per competitor"):
        AnalystArtifact.model_validate(art)
    art = lake_artifact(valid)
    art["pricing_matrix"] = [r for r in art["pricing_matrix"] if r["competitor_name"] != "Verizon Home Internet"]
    with using(LAKE), pytest.raises(ValidationError, match="Verizon Home Internet"):
        AnalystArtifact.model_validate(art)


def test_strategy_competitor_names_follow_the_brief(strategy):
    strategy["positioning"]["competitive_frame"] = ["Xfinity"]
    strategy["icps"][0]["current_provider"]["value"] = ["CenturyLink"]
    with using(LAKE):
        StrategyArtifact.model_validate(strategy)
    with pytest.raises(ValidationError):
        StrategyArtifact.model_validate(strategy)


def test_the_llm_is_given_the_lake_names_and_row_counts():
    with using(LAKE):
        s = output_models()["analyze_evidence"].model_json_schema()
    table, pricing = s["properties"]["competitor_comparison_table"], s["properties"]["pricing_matrix"]
    assert (table["minItems"], table["maxItems"], pricing["minItems"]) == (7, 7, 6) and "maxItems" not in pricing
    assert s["$defs"]["CompetitorComparisonRow"]["properties"]["competitor_name"]["enum"] == list(LAKE.all_names)


def test_the_wire_model_enforces_the_lake_row_count(valid):
    art = lake_artifact(valid)
    art["product_feature_comparison"] = art["product_feature_comparison"][:4]
    with using(LAKE):
        wire = output_models()["analyze_evidence"]
        with pytest.raises(ValidationError):
            wire.model_validate(art)


@pytest.mark.parametrize("ctx", [OCALA, LAKE], ids=["ocala", "lake-county"])
def test_every_prompt_marker_is_filled(ctx):
    for f in ("agents.yaml", "tasks.yaml"):
        with using(ctx):
            text = json.dumps(fill_config(yaml.safe_load((CONFIG_DIR / f).read_text())))
        assert "[[" not in text
        assert ("Ocala" in text) == (ctx is OCALA)


def test_lake_prompts_name_lake_competitors_and_carry_the_framing_rules():
    with using(LAKE):
        tasks = fill_config(yaml.safe_load((CONFIG_DIR / "tasks.yaml").read_text()))
        agents = fill_config(yaml.safe_load((CONFIG_DIR / "agents.yaml").read_text()))
    analyst, strat = tasks["analyze_evidence"]["description"], tasks["build_strategy"]["description"]
    assert '"Verizon Home Internet"' in analyst and "exactly 7" in analyst and "AT&T" not in analyst
    assert 'Map "Comcast" to "Xfinity".' in analyst
    assert "Leesburg and Mount Dora" in strat and "stigmatizing" in strat and "Ocala" not in strat
    assert "Ocala" not in json.dumps(agents) and "Quantum Fiber" in agents["analyst"]["backstory"]
