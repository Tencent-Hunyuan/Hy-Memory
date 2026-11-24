"""Regression: an invalid LLM emotion score must not become a maximal one."""

import pytest

from hy_memory.agent.emotion_analyzer import EmotionAnalyzer


@pytest.mark.parametrize(
    "min_v,max_v",
    [(-1.0, 1.0), (0.0, 1.0)],
)
def test_nan_clamps_to_neutral_not_the_upper_bound(min_v, max_v):
    result = EmotionAnalyzer._clamp(float("nan"), min_v, max_v)
    assert result == pytest.approx(0.0)
    assert min_v <= result <= max_v


def test_nan_string_from_llm_does_not_mark_memory_as_highly_arousing():
    valence = EmotionAnalyzer._clamp(float("nan"), -1.0, 1.0)
    arousal = EmotionAnalyzer._clamp(float("nan"), 0.0, 1.0)
    assert valence == pytest.approx(0.0)
    assert arousal == pytest.approx(0.0)
    assert not (
        arousal > 0.6 and valence < -0.3
    ), "a NaN score must not flip the sensitive-content gate"


@pytest.mark.parametrize(
    "value,min_v,max_v,expected",
    [
        (0.5, -1.0, 1.0, 0.5),
        (-2.0, -1.0, 1.0, -1.0),
        (2.0, 0.0, 1.0, 1.0),
        (float("inf"), -1.0, 1.0, 1.0),
        (float("-inf"), 0.0, 1.0, 0.0),
    ],
)
def test_finite_values_and_infinities_keep_existing_behaviour(
    value, min_v, max_v, expected
):
    assert EmotionAnalyzer._clamp(value, min_v, max_v) == pytest.approx(expected)
