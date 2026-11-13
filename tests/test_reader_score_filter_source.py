# -*- coding: utf-8 -*-
"""Regression: the output-layer score gate must use the effective retrieval source.

The default public output mapping keeps the retrieval channel in
``retrieval_source`` and stores ``"default"`` in ``source``. Reading only
``source`` therefore skipped the threshold for normal vector-store results
(issue #346).
"""

from hy_memory.pipelines.reader_base import _filter_normal_results_by_score


def _item(score, source="default", retrieval_source="vdb", **extra):
    return {
        "memory_id": "m",
        "content": "content",
        "score": score,
        "source": source,
        "retrieval_source": retrieval_source,
        **extra,
    }


def test_vdb_results_below_threshold_are_dropped_even_when_source_is_default():
    items = [_item(0.54), _item(0.51), _item(0.62)]
    assert _filter_normal_results_by_score(items, 0.6) == [items[2]]


def test_explicit_gated_source_without_retrieval_source_is_still_dropped():
    items = [_item(0.1, source="vdb", retrieval_source="")]
    assert _filter_normal_results_by_score(items, 0.6) == []


def test_gated_sources_other_than_vdb_are_dropped():
    items = [
        _item(0.1, retrieval_source="vdb_keyword_only"),
        _item(0.1, retrieval_source="temporal_day"),
    ]
    assert _filter_normal_results_by_score(items, 0.6) == []


def test_non_gated_channels_are_preserved():
    items = [
        _item(0.1, source="graph_relaxed", retrieval_source="graph"),
        _item(0.1, source="episode", retrieval_source="schema_l6"),
    ]
    assert _filter_normal_results_by_score(items, 0.6) == items


def test_missing_score_counts_as_zero():
    items = [{"source": "default", "retrieval_source": "vdb"}]
    assert _filter_normal_results_by_score(items, 0.6) == []
