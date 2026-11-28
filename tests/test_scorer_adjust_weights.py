# -*- coding: utf-8 -*-
"""adjust_weights() must honour an explicit 0.0 weight."""

from hy_memory.core.scorer import MemoryScorer


def test_explicit_zero_weight_disables_the_dimension():
    scorer = MemoryScorer()
    scorer.adjust_weights(semantic_weight=0.0)

    assert scorer.config.semantic_weight == 0.0
    # the remaining weights are renormalised over the dimensions still in play
    assert round(scorer.config.recency_weight, 6) == 0.6
    assert round(scorer.config.importance_weight, 6) == 0.3
    assert round(scorer.config.access_weight, 6) == 0.1


def test_omitted_weights_keep_the_current_value():
    scorer = MemoryScorer()
    scorer.adjust_weights(semantic_weight=0.25, recency_weight=0.25,
                          importance_weight=0.25, access_weight=0.25)
    scorer.adjust_weights()

    assert round(scorer.config.semantic_weight, 6) == 0.25
    assert round(scorer.config.recency_weight, 6) == 0.25
    assert round(scorer.config.importance_weight, 6) == 0.25
    assert round(scorer.config.access_weight, 6) == 0.25
