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
    guess = price_row("Wire3", post_promo_price_usd_per_month=cited(70.0, "inference"))
    valid["pricing_matrix"].append(guess)
    with pytest.raises(ValidationError, match="Wire3 row only with a post-promo price cited from"):
        AnalystArtifact.model_validate(valid)
    valid["pricing_matrix"][-1] = price_row("Wire3")  # evidence-cited price is fine
    AnalystArtifact.model_validate(valid)
