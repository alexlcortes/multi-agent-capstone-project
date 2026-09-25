"""Pydantic translation of schemas/analyst_artifact.schema.json.

Where this is stricter than the JSON Schema, it only enforces what the schema's
own descriptions already require (e.g. CitedField.value's expected type per
column, the promo_status rules, the fixed blended-price formula).

Grounding against a concrete evidence set (do the cited ids exist?) cannot live
in the model, so it is check_grounding() below, used as the task guardrail.

Competitor names and the per-competitor row counts come from the active brief
(brief_context; Ocala by default), so the same models serve every brief. Their
JSON schema is generated from the active brief too, which is what OpenAI's
constrained decoding sees.
"""

import re
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field, create_model, model_validator

from wire3_gtm import brief_context

EvidenceId = Annotated[str, Field(pattern=r"^EV-[a-f0-9]{8}$")]
RQId = Annotated[str, Field(pattern=r"^RQ[0-9]+$")]


class _CompetitorEnum:
    """JSON schema of a competitor name: the active brief's table spellings, exactly as a Literal of them."""

    def __get_pydantic_json_schema__(self, core_schema, handler):
        return {"enum": list(brief_context.active().all_names), "type": "string"}


def _known_competitor(name: str) -> str:
    names = brief_context.active().all_names
    if name not in names:
        raise ValueError(f"competitor_name must be one of {list(names)}, got {name!r}")
    return name


class _RowsPerCompetitor:
    """JSON schema minItems/maxItems for a table with one row per competitor of the active brief.
    Only the schema: the model validators enforce which names the rows have."""

    def __init__(self, priced_only: bool = False, exact: bool = True):
        self.priced_only, self.exact = priced_only, exact

    def bounds(self) -> dict:
        ctx = brief_context.active()
        n = len(ctx.competitors if self.priced_only else ctx.all_names)
        return {"min_length": n, "max_length": n} if self.exact else {"min_length": n}

    def wire_constraints(self) -> list:
        """What wire_model puts in its place: real length constraints for the active brief."""
        from annotated_types import MaxLen, MinLen

        b = self.bounds()
        return [MinLen(b["min_length"])] + ([MaxLen(b["max_length"])] if "max_length" in b else [])

    def __get_pydantic_json_schema__(self, core_schema, handler):
        schema = handler(core_schema)
        b = self.bounds()
        schema["minItems"] = b["min_length"]
        if "max_length" in b:
            schema["maxItems"] = b["max_length"]
        return schema


CompetitorName = Annotated[str, AfterValidator(_known_competitor), _CompetitorEnum()]
Basis = Literal["evidence", "brief_stated", "inference"]

BLENDED_FORMULA = (
    "((promo_price * promo_duration_months) + "
    "(post_promo_price * (12 - promo_duration_months))) / 12"
)
UNSOURCED_PROMO_FORMULA = "post_promo_price (promo length not sourced, so the promo is left out of the blend)"


class _CitedRules(BaseModel):
    """CitedField's allOf rule: basis 'evidence' requires non-empty evidence_ids."""

    @model_validator(mode="after")
    def _evidence_basis_needs_ids(self):
        if self.basis == "evidence" and not self.evidence_ids:  # type: ignore[attr-defined]
            raise ValueError("basis 'evidence' requires non-empty evidence_ids")
        return self


def _cited(name: str, value_type):
    """One CitedField per value type. The JSON Schema leaves `value` untyped and
    documents the expected type in each column's description; typing it here
    makes those descriptions enforced, and keeps OpenAI strict output usable."""
    return create_model(
        name,
        __base__=_CitedRules,
        value=(value_type, ...),
        evidence_ids=(list[EvidenceId], ...),
        basis=(Basis, ...),
    )


CitedStr = _cited("CitedStr", str)
CitedBool = _cited("CitedBool", bool)
CitedNumber = _cited("CitedNumber", float)
CitedNullableNumber = _cited("CitedNullableNumber", float | None)
CitedNullableInt = _cited("CitedNullableInt", int | None)
CitedStrList = _cited("CitedStrList", list[str])
CitedServiceType = _cited("CitedServiceType", Literal["cable", "fiber", "dsl", "fixed_wireless", "unknown"])
CitedTechType = _cited("CitedTechType", Literal["cable", "fiber", "dsl", "fixed_wireless"])
CitedPromoStatus = _cited("CitedPromoStatus", Literal["has_promo", "no_promo_found", "not_researched"])


class DerivedField(BaseModel):
    value: float
    derived_from_evidence_ids: list[EvidenceId] = Field(min_length=1)
    formula: str


class CompetitorComparisonRow(BaseModel):
    competitor_name: CompetitorName
    footprint_confirmed_in_region: CitedBool
    service_type: CitedServiceType
    has_mobile_bundle_available: CitedBool
    top_advertised_speed_mbps_down: CitedNumber
    top_advertised_speed_mbps_up: CitedNumber
    summary_note: CitedStr | None = None


class PricingMatrixRow(BaseModel):
    competitor_name: CompetitorName
    plan_name: CitedStr
    speed_mbps_down: CitedNumber
    promo_status: CitedPromoStatus
    promo_price_usd_per_month: CitedNullableNumber
    promo_duration_months: CitedNullableInt
    post_promo_price_usd_per_month: CitedNullableNumber
    equipment_fee_usd_per_month: CitedNullableNumber | None = None
    early_termination_fee_usd: CitedNullableNumber | None = None
    blended_12mo_effective_price_usd: DerivedField

    @model_validator(mode="after")
    def _promo_rules_and_blended_price(self):
        status = self.promo_status.value
        post_field = self.post_promo_price_usd_per_month
        post = post_field.value
        if status == "has_promo" and (
            self.promo_price_usd_per_month.value is None
            or self.promo_duration_months.value is None
        ):
            raise ValueError("promo_status 'has_promo' requires promo price and duration")
        if status == "no_promo_found" and not self.promo_status.evidence_ids:
            raise ValueError("'no_promo_found' must cite the page that was checked")

        # A price is a sourced fact: an 'inference' price is a guess, and a guessed
        # post-promo price (live run: set equal to the promo price) erases the promo
        # cliff the strategy is built on. Unknown means null, not a made-up number.
        if post is not None and post_field.basis == "inference":
            raise ValueError(
                "post_promo_price_usd_per_month must be cited from evidence, or null if unknown: "
                "never an inferred number"
            )

        # The blended price is computed, not sourced: apply the fixed formula in
        # code so both implementations get identical numbers from identical
        # inputs, instead of trusting the LLM's arithmetic. Duration is capped at
        # 12 because this is a 12-month figure.
        formula = BLENDED_FORMULA
        if status == "has_promo" and self.promo_duration_months.basis == "inference":
            # A guessed promo length would set the blend's weights (live run: Quantum Fiber's "1 month").
            # Only a sourced length counts; otherwise the blend is the post-promo price for all 12 months.
            if post is None:
                raise ValueError(
                    "promo_duration_months is inferred, so the 12-month blended price needs an evidence-cited "
                    "post-promo price; cite the promo length or that price from the evidence, or omit this plan"
                )
            promo, months, formula = post, 0, UNSOURCED_PROMO_FORMULA
        elif status == "has_promo":
            promo, months = self.promo_price_usd_per_month.value, min(self.promo_duration_months.value, 12)
            if months < 12 and post is None:
                raise ValueError(
                    "a promo shorter than 12 months needs an evidence-cited post-promo price to compute the "
                    "12-month blended price; find that price in the evidence or omit this plan"
                )
        else:
            if post is None:
                raise ValueError("a plan with no promo and no known price cannot be priced: omit this plan")
            promo, months = post, 0
        # With a 12-month promo the post-promo price has zero weight, so it may be null.
        self.blended_12mo_effective_price_usd.value = round(
            (promo * months + (post or 0.0) * (12 - months)) / 12, 2
        )
        self.blended_12mo_effective_price_usd.formula = formula
        return self


class AdditionalFeature(BaseModel):
    name: str
    field: CitedStr


class ProductFeatureRow(BaseModel):
    competitor_name: CompetitorName
    technology_type: CitedTechType
    symmetric_speeds: CitedBool
    data_cap_present: CitedBool
    wifi_router_included: CitedBool
    professional_install_included: CitedBool
    self_install_available: CitedBool
    contract_required: CitedBool
    contract_length_months: CitedNullableInt
    customer_support_channels: CitedStrList
    additional_features: list[AdditionalFeature] = []


class MarketTheme(BaseModel):
    theme_id: str = Field(pattern=r"^THEME-[0-9]+$")
    title: str = Field(max_length=80)
    description: str
    related_research_question_ids: list[RQId] = Field(min_length=1)
    supporting_evidence_ids: list[EvidenceId] = Field(min_length=1)
    competitors_involved: list[CompetitorName] = []


class _SwotItemBase(BaseModel):
    statement: str
    basis: Basis
    evidence_ids: list[EvidenceId]

    @model_validator(mode="after")
    def _evidence_basis_needs_ids(self):
        if self.basis == "evidence" and not self.evidence_ids:
            raise ValueError("basis 'evidence' requires non-empty evidence_ids")
        return self


class SwotItem(_SwotItemBase):
    item_id: str = Field(pattern=r"^SWOT-(S|W|O|T)[0-9]+$")


class Swot(BaseModel):
    strengths: list[SwotItem] = Field(min_length=1)
    weaknesses: list[SwotItem] = Field(min_length=1)
    opportunities: list[SwotItem] = Field(min_length=1)
    threats: list[SwotItem] = Field(min_length=1)


class SevenPCategoryEntry(BaseModel):
    market_description: CitedStr
    wire3_relevant_fact: CitedStr | None = None


class SevenP(BaseModel):
    product: SevenPCategoryEntry
    price: SevenPCategoryEntry
    place: SevenPCategoryEntry
    promotion: SevenPCategoryEntry
    people: SevenPCategoryEntry
    process: SevenPCategoryEntry
    physical_evidence: SevenPCategoryEntry


class Assumption(BaseModel):
    id: str = Field(pattern=r"^ASM-[0-9]+$")
    statement: str
    rationale: str
    evidence_ids: list[EvidenceId]


class Unknown(BaseModel):
    id: str = Field(pattern=r"^UNK-[0-9]+$")
    research_question_id: RQId
    description: str
    reason_unresolved: str


class Conflict(BaseModel):
    id: str = Field(pattern=r"^CONF-[0-9]+$")
    research_question_id: RQId
    evidence_id_a: EvidenceId
    evidence_id_b: EvidenceId
    description: str
    resolution_status: Literal[
        "unresolved", "resolved_favor_a", "resolved_favor_b", "both_retained_as_uncertain"
    ]


class AssumptionsUnknownsConflicts(BaseModel):
    assumptions: list[Assumption]
    unknowns: list[Unknown]
    conflicts: list[Conflict]


class AnalystArtifact(BaseModel):
    brief_id: str
    competitor_comparison_table: Annotated[list[CompetitorComparisonRow], _RowsPerCompetitor()]
    # DELIBERATE deviation from analyst_artifact.schema.json (minItems 4, "one row per CompetitorName"):
    # the schema counts Wire3, but Wire3's price is not public and the brief forbids inventing it, so a
    # priced Wire3 row usually cannot exist. The minimum is one row per priced competitor (3 for Ocala),
    # which _competitor_coverage enforces. n8n's artifact already has 3 rows (its prompt makes Wire3 optional).
    pricing_matrix: Annotated[list[PricingMatrixRow], _RowsPerCompetitor(priced_only=True, exact=False)]
    product_feature_comparison: Annotated[list[ProductFeatureRow], _RowsPerCompetitor()]
    market_themes: list[MarketTheme] = Field(min_length=3, max_length=6)
    swot: Swot
    seven_p_analysis: SevenP
    assumptions_unknowns_conflicts: AssumptionsUnknownsConflicts

    @model_validator(mode="after")
    def _competitor_coverage(self):
        ctx = brief_context.active()
        for table in ("competitor_comparison_table", "product_feature_comparison"):
            names = [r.competitor_name for r in getattr(self, table)]
            if sorted(names) != sorted(ctx.all_names):
                raise ValueError(f"{table} must have exactly one row per competitor, got {names}")
        priced = {r.competitor_name for r in self.pricing_matrix}
        if missing := set(ctx.competitors) - priced:
            raise ValueError(f"pricing_matrix missing rows for {sorted(missing)}")
        for r in self.pricing_matrix:
            price = r.post_promo_price_usd_per_month
            if r.competitor_name == "Wire3" and price.basis != "evidence":
                raise ValueError(
                    "pricing_matrix may include a Wire3 row only with a post-promo price cited from "
                    "evidence; do not use a placeholder price. Omit the Wire3 row and add more "
                    "competitor plan tiers instead"
                )
        wire3 = next(r for r in self.competitor_comparison_table if r.competitor_name == "Wire3")
        bundle = wire3.has_mobile_bundle_available
        if bundle.basis != "brief_stated" or bundle.value is not False:
            raise ValueError("Wire3 has_mobile_bundle_available must be false with basis 'brief_stated'")
        return self


def dump_contract(node):
    """JSON-ready dict matching the .schema.json exactly: optional fields that
    were left out come back as None from the model, but the schema forbids null
    there, so they are dropped. Required fields (even null-valued ones) stay."""
    if isinstance(node, BaseModel):
        out = {}
        for name, field in type(node).model_fields.items():
            value = getattr(node, name)
            if value is None and not field.is_required():
                continue
            out[name] = dump_contract(value)
        return out
    if isinstance(node, list):
        return [dump_contract(i) for i in node]
    return node


# --- grounding against the actual evidence set --------------------------------

_ID_KEYS = {
    "evidence_ids", "derived_from_evidence_ids", "supporting_evidence_ids",
    "evidence_id_a", "evidence_id_b",
}


def cited_evidence_ids(node) -> set[str]:
    """Every evidence id cited anywhere in the (dumped) artifact."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _ID_KEYS:
                found.update(value if isinstance(value, list) else [value])
            else:
                found |= cited_evidence_ids(value)
    elif isinstance(node, list):
        for item in node:
            found |= cited_evidence_ids(item)
    return found


def check_grounding(artifact: AnalystArtifact, valid_ids: set[str]) -> list[str]:
    """Errors for any cited evidence_id that is not in the evidence set, including ids written into
    prose (an A/B run wrote a near-copy id inside an unknown's reason_unresolved; the id fields were
    clean, so it passed here and the Docs Writer refused the document at the very end)."""
    invented = sorted(cited_evidence_ids(artifact.model_dump()) - valid_ids)
    return [f"evidence_id {i} is not in the evidence set" for i in invented] + prose_id_errors(
        artifact.model_dump(), valid_ids)


_EV_IN_TEXT = re.compile(r"\bEV-[0-9a-f]{8}\b")


def prose_id_errors(doc, valid_ids: set[str], path: str = "") -> list[str]:
    """Evidence ids mentioned inside free-text fields that are not real ids. Id fields themselves
    (evidence_ids, evidence_id_a, ...) are checked by the callers' own id checks."""
    errors = []
    if isinstance(doc, dict):
        for k, v in doc.items():
            if "evidence_id" not in k and k != "supporting_ids":
                errors += prose_id_errors(v, valid_ids, f"{path}.{k}" if path else k)
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            errors += prose_id_errors(v, valid_ids, f"{path}[{i}]")
    elif isinstance(doc, str):
        for bad in sorted(set(_EV_IN_TEXT.findall(doc)) - valid_ids):
            errors.append(f"{path} mentions {bad} in its text, which is not an id in the evidence set: "
                          "copy ids exactly or leave them out of prose")
    return errors
