"""Briefs with customer_segments get one ICP per segment, named after it (Ocala: no rule)."""

import copy

from test_analyst_models import valid  # noqa: F401
from test_brief_context import LAKE
from test_strategy import c, strategy  # noqa: F401

from wire3_gtm.brief_context import using
from wire3_gtm.strategy_checks import report, segment_errors, shared_channel_icps
from wire3_gtm.strategy_models import StrategyArtifact


def two_segment_strategy(strategy, names=("Cost-constrained households: renters", "Value switchers: homeowners")):
    s = copy.deepcopy(strategy)
    s["icps"] = [dict(s["icps"][0], icp_id=f"ICP-{i + 1}", name=n) for i, n in enumerate(names)]
    s["channels"] = [dict(s["channels"][0], channel_id="CHANNEL-1", target_icp_ids=["ICP-1"]),
                     dict(s["channels"][0], channel_id="CHANNEL-2", target_icp_ids=["ICP-2"])]
    return StrategyArtifact.model_validate(s)


def test_lake_brief_defines_two_segments():
    assert [n for n, _ in LAKE.customer_segments] == ["Cost-constrained households", "Value switchers"]
    assert "primary barrier" in LAKE.segment_rule


def test_one_icp_per_segment_passes(strategy):
    with using(LAKE):
        assert segment_errors(two_segment_strategy(strategy)) == []


def test_a_drifted_icp_is_sent_back_with_the_segment_name(strategy):
    art = two_segment_strategy(strategy, ("Budget-conscious households", "Value-seeking heavy users"))
    with using(LAKE):
        errors = segment_errors(art)
    assert any("'Cost-constrained households'" in e for e in errors) and any("'Value switchers'" in e for e in errors)


def test_wrong_icp_count_is_an_error(strategy):
    with using(LAKE):
        assert any("exactly 2 ICPs" in e for e in segment_errors(StrategyArtifact.model_validate(strategy)))


def test_ocala_has_no_segment_rule(strategy):
    assert segment_errors(StrategyArtifact.model_validate(strategy)) == []
    assert "icps_with_identical_channels" not in report(StrategyArtifact.model_validate(strategy))


def test_identical_channel_sets_are_reported(strategy):
    art = two_segment_strategy(strategy)
    assert shared_channel_icps(art) == []
    same = art.model_copy(deep=True)
    for ch in same.channels:
        ch.target_icp_ids = ["ICP-1", "ICP-2"]
    assert shared_channel_icps(same) == ["ICP-1", "ICP-2"]
    with using(LAKE):
        assert report(same)["icps_with_identical_channels"] == ["ICP-1", "ICP-2"]
