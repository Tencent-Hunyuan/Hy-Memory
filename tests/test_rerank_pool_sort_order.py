"""The post-rerank pool must not treat a genuine 0.0 score as missing.

``_read_once`` used ``-float(item.get("_local_rerank_score") or float("-inf"))``
as its sort key. ``0.0`` is a valid reranker score but is falsy, so it collapsed
to ``-inf`` and sorted behind negative scores -- the item was then dropped
first when the pool was truncated to top-K. ``_reranked_pool_sort_key`` keeps
the zero in its rightful place while still sorting unscored items last.
"""

from hy_memory.pipelines.reader_base import _reranked_pool_sort_key


def _rank(items):
    return [
        item["node_id"]
        for item in sorted(items, key=_reranked_pool_sort_key)
    ]


def test_zero_score_ranks_above_negative_scores():
    """A reranker score of exactly 0.0 is better than a negative one."""
    items = [
        {"node_id": "pos", "_local_rerank_score": 0.8},
        {"node_id": "zero", "_local_rerank_score": 0.0},
        {"node_id": "neg", "_local_rerank_score": -0.3},
    ]
    assert _rank(items) == ["pos", "zero", "neg"]


def test_unscored_item_still_sorts_last():
    """An item with no score keeps the -inf fallback and stays at the end."""
    items = [
        {"node_id": "zero", "_local_rerank_score": 0.0},
        {"node_id": "neg", "_local_rerank_score": -0.3},
        {"node_id": "unscored"},
    ]
    assert _rank(items) == ["zero", "neg", "unscored"]


def test_equal_scores_keep_node_id_order():
    """The node-id tie-breaker is unchanged."""
    items = [
        {"node_id": "b", "_local_rerank_score": 0.5},
        {"node_id": "a", "_local_rerank_score": 0.5},
    ]
    assert _rank(items) == ["a", "b"]
