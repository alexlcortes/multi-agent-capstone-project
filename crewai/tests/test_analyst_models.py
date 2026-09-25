import copy
import json
from pathlib import Path

import jsonschema
import pytest
from pydantic import ValidationError

from wire3_gtm.analyst_models import AnalystArtifact, check_grounding, dump_contract

SCHEMA = json.loads((Path(__file__).parents[2] / "schemas/analyst_artifact.schema.json").read_text())
EV = ["EV-aaaaaaaa", "EV-bbbbbbbb"]
NAMES = ["Wire3", "Spectrum", "AT&T", "T-Mobile Home Internet"]


def cited(value, basis="evidence", ids=None):
    return {"value": value, "evidence_ids": EV[:1] if ids is None and basis == "evidence" else (ids or []), "basis": basis}


def comp_row(name):
    wire3 = name == "Wire3"
    return {
        "competitor_name": name,
        "footprint_confirmed_in_region": cited(True),
        "service_type": cited("fiber", "brief_stated") if wire3 else cited("cable"),
        "has_mobile_bundle_available": cited(False, "brief_stated") if wire3 else cited(True),
        "top_advertised_speed_mbps_down": cited(1000.0),
        "top_advertised_speed_mbps_up": cited(1000.0, "inference"),
    }


def price_row(name, **over):
    row = {
        "competitor_name": name,
        "plan_name": cited("Internet 300"),
        "speed_mbps_down": cited(300.0),
        "promo_status": cited("has_promo"),
        "promo_price_usd_per_month": cited(30.0),
        "promo_duration_months": cited(12),
        "post_promo_price_usd_per_month": cited(60.0),
        "blended_12mo_effective_price_usd": {"value": 999.0, "derived_from_evidence_ids": EV[:1], "formula": "wrong"},
    }
    row.update(over)
    return row


def feature_row(name):
    return {
        "competitor_name": name,
        "technology_type": cited("fiber"),
        "symmetric_speeds": cited(True),
        "data_cap_present": cited(False),
        "wifi_router_included": cited(True),
        "professional_install_included": cited(True),
        "self_install_available": cited(True),
        "contract_required": cited(False),
        "contract_length_months": cited(None),
        "customer_support_channels": cited(["phone", "chat"]),
    }


def entry():
    return {"market_description": cited("described")}


@pytest.fixture
def valid():
    return {
        "brief_id": "run-1",
        "competitor_comparison_table": [comp_row(n) for n in NAMES],
        "pricing_matrix": [price_row(n) for n in NAMES[1:]] + [price_row("Spectrum")],
        "product_feature_comparison": [feature_row(n) for n in NAMES],
        "market_themes": [
            {"theme_id": f"THEME-{i}", "title": "t", "description": "d",
             "related_research_question_ids": ["RQ2"], "supporting_evidence_ids": EV[:1]}
            for i in (1, 2, 3)
        ],
        "swot": {k: [{"item_id": f"SWOT-{k[0].upper()}1", "statement": "s", "basis": "inference", "evidence_ids": []}]
                 for k in ("strengths", "weaknesses", "opportunities", "threats")},
        "seven_p_analysis": {k: entry() for k in
                             ("product", "price", "place", "promotion", "people", "process", "physical_evidence")},
        "assumptions_unknowns_conflicts": {"assumptions": [], "unknowns": [], "conflicts": []},
    }


def test_valid_artifact_passes_pydantic_and_original_json_schema(valid):
    artifact = AnalystArtifact.model_validate(valid)
    jsonschema.validate(dump_contract(artifact), SCHEMA)


def test_basis_evidence_with_no_ids_is_rejected(valid):
    valid["competitor_comparison_table"][1]["footprint_confirmed_in_region"]["evidence_ids"] = []
    with pytest.raises(ValidationError, match="requires non-empty evidence_ids"):
        AnalystArtifact.model_validate(valid)


def test_variant_competitor_spelling_is_rejected(valid):
    valid["competitor_comparison_table"][2]["competitor_name"] = "AT&T Fiber"
    with pytest.raises(ValidationError):
        AnalystArtifact.model_validate(valid)


def test_duplicate_competitor_row_is_rejected(valid):
    valid["product_feature_comparison"][3] = feature_row("AT&T")
    with pytest.raises(ValidationError, match="exactly one row per competitor"):
        AnalystArtifact.model_validate(valid)


def test_wire3_mobile_bundle_must_be_brief_stated_false(valid):
    valid["competitor_comparison_table"][0]["has_mobile_bundle_available"] = cited(False)
    with pytest.raises(ValidationError, match="brief_stated"):
        AnalystArtifact.model_validate(valid)


def test_blended_price_is_computed_from_formula_not_trusted(valid):
    artifact = AnalystArtifact.model_validate(valid)
    # 30*12 + 60*0 over 12 months
    assert artifact.pricing_matrix[0].blended_12mo_effective_price_usd.value == 30.0
    valid["pricing_matrix"][0]["promo_duration_months"] = cited(6)
    artifact = AnalystArtifact.model_validate(valid)
    assert artifact.pricing_matrix[0].blended_12mo_effective_price_usd.value == 45.0  # (30*6+60*6)/12


def test_no_promo_uses_post_promo_price(valid):
    valid["pricing_matrix"][0].update(
        promo_status=cited("no_promo_found"),
        promo_price_usd_per_month=cited(None, "inference"),
        promo_duration_months=cited(None, "inference"),
    )
    artifact = AnalystArtifact.model_validate(valid)
    assert artifact.pricing_matrix[0].blended_12mo_effective_price_usd.value == 60.0


def test_has_promo_requires_price_and_duration(valid):
    valid["pricing_matrix"][0]["promo_price_usd_per_month"] = cited(None, "inference")
    with pytest.raises(ValidationError, match="has_promo"):
        AnalystArtifact.model_validate(valid)


def test_invented_evidence_id_is_caught_by_grounding(valid):
    artifact = AnalystArtifact.model_validate(valid)
    assert check_grounding(artifact, set(EV)) == []
    assert check_grounding(artifact, {"EV-bbbbbbbb"})  # EV-aaaaaaaa now "not in the set"


def test_wire3_pricing_row_needs_an_evidence_cited_price(valid):
    guess = price_row("Wire3", post_promo_price_usd_per_month=cited(None, "inference"))  # unknown price, not evidence
    valid["pricing_matrix"].append(guess)
    with pytest.raises(ValidationError, match="Wire3 row only with a post-promo price cited from"):
        AnalystArtifact.model_validate(valid)
    valid["pricing_matrix"][-1] = price_row("Wire3")  # evidence-cited price is fine
    AnalystArtifact.model_validate(valid)


# --- prices are sourced facts, never guesses ---------------------------------

def test_an_inferred_post_promo_price_is_rejected(valid):
    valid["pricing_matrix"][0]["post_promo_price_usd_per_month"] = cited(30.0, "inference")  # the live-run flaw
    with pytest.raises(ValidationError, match="never an inferred number"):
        AnalystArtifact.model_validate(valid)


def test_unknown_post_promo_price_is_null_and_a_12_month_promo_still_blends(valid):
    valid["pricing_matrix"][0]["post_promo_price_usd_per_month"] = cited(None, "inference")
    artifact = AnalystArtifact.model_validate(valid)
    row = artifact.pricing_matrix[0]
    assert row.post_promo_price_usd_per_month.value is None
    assert row.blended_12mo_effective_price_usd.value == 30.0  # 12 months at the promo price
    jsonschema.validate(dump_contract(artifact), SCHEMA)  # null is valid: the schema leaves `value` untyped


def test_a_short_promo_needs_an_evidenced_post_promo_price(valid):
    row = valid["pricing_matrix"][0]
    row["promo_duration_months"] = cited(6)
    row["post_promo_price_usd_per_month"] = cited(None, "inference")
    with pytest.raises(ValidationError, match="shorter than 12 months"):
        AnalystArtifact.model_validate(valid)
    row["post_promo_price_usd_per_month"] = cited(60.0)  # evidence-cited: fine
    assert AnalystArtifact.model_validate(valid).pricing_matrix[0].blended_12mo_effective_price_usd.value == 45.0


def test_a_plan_with_no_promo_and_no_known_price_is_rejected(valid):
    valid["pricing_matrix"][0].update(
        promo_status=cited("no_promo_found"),
        promo_price_usd_per_month=cited(None, "inference"),
        promo_duration_months=cited(None, "inference"),
        post_promo_price_usd_per_month=cited(None, "inference"),
    )
    with pytest.raises(ValidationError, match="cannot be priced"):
        AnalystArtifact.model_validate(valid)


def test_a_guessed_promo_length_is_left_out_of_the_blend(valid):
    # Live Lake run: Quantum Fiber's $50 promo was given an inferred "1 month" before an $80 price.
    row = valid["pricing_matrix"][0]
    row.update(promo_price_usd_per_month=cited(50.0), promo_duration_months=cited(1, "inference"),
               post_promo_price_usd_per_month=cited(80.0))
    blended = AnalystArtifact.model_validate(valid).pricing_matrix[0].blended_12mo_effective_price_usd
    assert blended.value == 80.0 and "not sourced" in blended.formula
    row["post_promo_price_usd_per_month"] = cited(None, "inference")
    with pytest.raises(ValidationError, match="promo_duration_months is inferred"):
        AnalystArtifact.model_validate(valid)
