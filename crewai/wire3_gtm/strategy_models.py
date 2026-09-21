"""Pydantic translation of schemas/strategy_artifact.schema.json.

Same approach as analyst_models: typed `value` per column (the schema documents
the type in each column's description), the StrategyCitedField allOf rule as a
validator, and referential integrity between the artifact's own ids (ICP refs,
phase order) checked in code. Whether supporting_ids point at real Analyst
findings depends on a concrete Analyst artifact, so that is strategy_checks.py.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field, create_model, model_validator

from wire3_gtm.analyst_models import Basis, CompetitorName

SupportingRef = Annotated[
    str, Field(pattern=r"^(EV-[a-f0-9]{8}|THEME-[0-9]+|SWOT-(S|W|O|T)[0-9]+|ASM-[0-9]+|UNK-[0-9]+)$")
]
IcpId = Annotated[str, Field(pattern=r"^ICP-[0-9]+$")]
Level = Literal["high", "medium", "low"]


class _CitedRules(BaseModel):
    """StrategyCitedField's allOf rule: basis 'evidence' needs supporting_ids."""

    @model_validator(mode="after")
    def _evidence_basis_needs_ids(self):
        if self.basis == "evidence" and not self.supporting_ids:  # type: ignore[attr-defined]
            raise ValueError("basis 'evidence' requires non-empty supporting_ids")
        return self


def _cited(name: str, value_type):
    return create_model(
        name,
        __base__=_CitedRules,
        value=(value_type, ...),
        supporting_ids=(list[SupportingRef], ...),
        basis=(Basis, ...),
    )


StratStr = _cited("StratStr", str)
StratLevel = _cited("StratLevel", Level)
StratCompetitors = _cited("StratCompetitors", list[CompetitorName])
StratStrOrNumber = _cited("StratStrOrNumber", str | float)


class Icp(BaseModel):
    icp_id: str = Field(pattern=r"^ICP-[0-9]+$")
    name: str
    description: StratStr
    current_provider: StratCompetitors
    demographic_signals: list[StratStr] = []


class PainOutcome(BaseModel):
    pain_id: str = Field(pattern=r"^PAIN-[0-9]+$")
    pain_statement: StratStr
    desired_outcome: StratStr
    related_icp_ids: list[IcpId] = Field(min_length=1)
    severity: StratLevel


class ValueProposition(BaseModel):
    headline: StratStr
    supporting_points: list[StratStr] = Field(min_length=1)
    # Required, not optional: the brief demands both gaps be addressed explicitly.
    no_bundle_gap_response: StratStr
    low_brand_recognition_response: StratStr


class Positioning(BaseModel):
    positioning_statement: StratStr
    differentiators: list[StratStr] = Field(min_length=1)
    competitive_frame: list[CompetitorName] = Field(min_length=1)


class MessagePillar(BaseModel):
    pillar_id: str = Field(pattern=r"^PILLAR-[0-9]+$")
    title: str = Field(max_length=60)
    supporting_message: StratStr
    target_icp_ids: list[IcpId] = Field(min_length=1)


class ChannelRecommendation(BaseModel):
    channel_id: str = Field(pattern=r"^CHANNEL-[0-9]+$")
    channel_name: str
    channel_category: Literal["digital_paid", "digital_organic", "local_offline", "partnership", "referral"]
    budget_tier: Literal["low", "medium", "high"]
    rationale: StratStr
    target_icp_ids: list[IcpId] = Field(min_length=1)


class LaunchPhase(BaseModel):
    phase_id: str = Field(pattern=r"^PHASE-[0-9]+$")
    phase_name: str
    sequence_order: int = Field(ge=1)
    timeframe: str
    activities: list[StratStr] = Field(min_length=1)
    exit_criteria: str


class SuccessMetric(BaseModel):
    metric_id: str = Field(pattern=r"^METRIC-[0-9]+$")
    name: str
    definition: str
    target_value: StratStrOrNumber
    measurement_cadence: Literal["weekly", "monthly", "quarterly"]


class Risk(BaseModel):
    risk_id: str = Field(pattern=r"^RISK-[0-9]+$")
    statement: StratStr
    likelihood: StratLevel
    impact: StratLevel
    mitigation: StratStr


class FollowUpResearchQuestion(BaseModel):
    frq_id: str = Field(pattern=r"^FRQ-[0-9]+$")
    question: str
    rationale: str
    related_unknown_ids: list[str] = Field(default=[])
    priority: Level

    @model_validator(mode="after")
    def _unknown_ids_look_like_unknown_ids(self):
        import re
        bad = [i for i in self.related_unknown_ids if not re.fullmatch(r"UNK-[0-9]+", i)]
        if bad:
            raise ValueError(f"related_unknown_ids must be UNK-n ids, got {bad}")
        return self


class StrategyArtifact(BaseModel):
    brief_id: str
    icps: list[Icp] = Field(min_length=1, max_length=3)
    pains_and_outcomes: list[PainOutcome] = Field(min_length=1)
    value_proposition: ValueProposition
    positioning: Positioning
    message_pillars: list[MessagePillar] = Field(min_length=3, max_length=5)
    channels: list[ChannelRecommendation] = Field(min_length=1)
    launch_phases: list[LaunchPhase] = Field(min_length=1)
    success_metrics: list[SuccessMetric] = Field(min_length=1)
    risks: list[Risk] = Field(min_length=1)
    follow_up_research_questions: list[FollowUpResearchQuestion]

    @model_validator(mode="after")
    def _internal_references_resolve(self):
        icp_ids = {i.icp_id for i in self.icps}
        refs = (
            [(p.pain_id, r) for p in self.pains_and_outcomes for r in p.related_icp_ids]
            + [(m.pillar_id, r) for m in self.message_pillars for r in m.target_icp_ids]
            + [(c.channel_id, r) for c in self.channels for r in c.target_icp_ids]
        )
        dangling = sorted({f"{owner} -> {ref}" for owner, ref in refs if ref not in icp_ids})
        if dangling:
            raise ValueError(f"references to ICPs that do not exist: {dangling}; ICPs are {sorted(icp_ids)}")
        orders = [p.sequence_order for p in self.launch_phases]
        if len(set(orders)) != len(orders):
            raise ValueError(f"launch_phases sequence_order values must be unique, got {orders}")
        return self
