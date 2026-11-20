# -*- coding: utf-8 -*-
"""Regression tests for MemoryScorer.adjust_weights() zero-weight handling.

A caller must be able to explicitly disable a scoring dimension by passing
``0.0``. The previous implementation used ``value or current``, which treats
a legitimate ``0.0`` as "not supplied" and keeps the old weight.
"""

import pytest

from hy_memory.config import RecallConfig
from hy_memory.core.scorer import MemoryScorer


def _weights(scorer):
    cfg = scorer.config
    return (
        cfg.semantic_weight,
        cfg.recency_weight,
        cfg.importance_weight,
        cfg.access_weight,
    )


def test_zero_semantic_weight_is_applied():
    scorer = MemoryScorer()
    scorer.adjust_weights(semantic_weight=0.0)
    assert scorer.config.semantic_weight == 0.0


@pytest.mark.parametrize(
    "field",
    ["semantic_weight", "recency_weight", "importance_weight", "access_weight"],
)
def test_each_weight_can_be_set_to_zero(field):
    scorer = MemoryScorer()
    scorer.adjust_weights(**{field: 0.0})
    assert getattr(scorer.config, field) == 0.0


def test_all_but_one_weight_can_be_zero_and_normalization_still_holds():
    scorer = MemoryScorer()
    scorer.adjust_weights(
        semantic_weight=1.0,
        recency_weight=0.0,
        importance_weight=0.0,
        access_weight=0.0,
    )
    assert _weights(scorer) == (1.0, 0.0, 0.0, 0.0)


def test_omitted_weights_keep_their_configured_values():
    config = RecallConfig(
        semantic_weight=0.4,
        recency_weight=0.25,
        importance_weight=0.25,
        access_weight=0.1,
    )
    scorer = MemoryScorer(config=config)
    scorer.adjust_weights(importance_weight=0.0)
    # Only the supplied field changes; the others are normalized but keep
    # their relative proportions from the provided config.
    assert scorer.config.importance_weight == 0.0
    assert scorer.config.semantic_weight > 0
    assert scorer.config.recency_weight > 0
    assert scorer.config.access_weight > 0
