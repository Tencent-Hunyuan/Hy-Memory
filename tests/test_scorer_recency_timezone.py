# -*- coding: utf-8 -*-
"""Regression: scoring a memory with a timezone-aware event time must not crash.

score() defaults reference_time to a naive datetime.now(), while event times
from history_store / embed_service can be timezone-aware. Subtracting the two
raised TypeError and aborted scoring for that memory entirely.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import math

from hy_memory.core.scorer import MemoryScorer


def _entry(created_at):
    return SimpleNamespace(created_at=created_at, confidence=0.9, access_count=2)


def test_aware_utc_event_time_does_not_crash():
    scorer = MemoryScorer()
    entry = _entry(datetime.now(timezone.utc) - timedelta(days=3))
    result = scorer.score(entry, semantic_score=0.8)
    assert 0.0 < result.recency_score < 1.0


def test_aware_non_utc_offset_does_not_crash():
    scorer = MemoryScorer()
    tz = timezone(timedelta(hours=8))
    entry = _entry(datetime.now(tz) - timedelta(hours=6))
    result = scorer.score(entry, semantic_score=0.8)
    assert result.recency_score > 0.0


def test_naive_event_time_still_works():
    scorer = MemoryScorer()
    entry = _entry(datetime.now() - timedelta(days=3))
    result = scorer.score(entry, semantic_score=0.8)
    assert 0.0 < result.recency_score < 1.0


def test_aware_future_event_still_scores_max():
    scorer = MemoryScorer()
    entry = _entry(datetime.now(timezone.utc) + timedelta(hours=1))
    result = scorer.score(entry, semantic_score=0.8)
    assert result.recency_score == 1.0
