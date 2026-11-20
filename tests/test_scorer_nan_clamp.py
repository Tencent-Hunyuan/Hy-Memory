# -*- coding: utf-8 -*-
"""Regression tests: NaN scores must not be clamped to the maximum.

``max(0.0, min(1.0, x))`` returns 1.0 for NaN because every comparison with
NaN is False, so a NaN semantic score silently became a perfect score and
polluted the final ranking. Infinity values are clamped correctly and are
included here as controls.
"""

from types import SimpleNamespace

from hy_memory.core.scorer import MemoryScorer


def test_nan_semantic_score_is_treated_as_zero():
    scorer = MemoryScorer()
    result = scorer.score(SimpleNamespace(), semantic_score=float("nan"))
    assert result.semantic_score == 0.0


def test_nan_access_count_is_treated_as_zero():
    scorer = MemoryScorer()
    entry = SimpleNamespace(access_count=float("nan"))
    assert scorer._calc_access_score(entry) == 0.0


def test_positive_infinity_clamps_to_one():
    scorer = MemoryScorer()
    result = scorer.score(SimpleNamespace(), semantic_score=float("inf"))
    assert result.semantic_score == 1.0


def test_negative_infinity_clamps_to_zero():
    scorer = MemoryScorer()
    result = scorer.score(SimpleNamespace(), semantic_score=float("-inf"))
    assert result.semantic_score == 0.0
