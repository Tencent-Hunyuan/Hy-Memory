"""Regression tests for the capped equal-score branch of BM25 stable ties."""

from types import SimpleNamespace

from hy_memory.utils.keyword_ties import bounded_keyword_ties


def _point(score, content):
    return SimpleNamespace(
        score=score,
        payload={
            "content": content,
            "layer": "L1",
            "session_id": "session",
            "memory_at": "1",
            "tags": [],
        },
    )


def _fetch_all(points):
    return lambda limit: points[:limit]


def test_entire_lookahead_is_one_tie_group_still_returns_top_k():
    """A popular query with >cap equal-score hits must not return nothing."""
    points = [_point(1.0, f"memory-{i:03d}") for i in range(200)]
    result = bounded_keyword_ties(_fetch_all(points), 5)
    assert [p.payload["content"] for p in result] == [
        "memory-000", "memory-001", "memory-002", "memory-003", "memory-004",
    ]


def test_tie_group_answer_is_stable_regardless_of_fetch_order():
    """Within one lookahead window, ordering must not depend on fetch order."""
    window = [_point(1.0, f"memory-{i:03d}") for i in range(17)]
    left = bounded_keyword_ties(_fetch_all(window), 4)
    right = bounded_keyword_ties(_fetch_all(list(reversed(window))), 4)
    assert [p.payload["content"] for p in left] == [
        "memory-000", "memory-001", "memory-002", "memory-003",
    ]
    assert [p.payload["content"] for p in left] == [
        p.payload["content"] for p in right
    ]


def test_distinct_boundary_behaviour_is_unchanged():
    points = (
        [_point(2.0, f"high-{i}") for i in range(3)]
        + [_point(1.0, f"low-{i}") for i in range(50)]
    )
    result = bounded_keyword_ties(_fetch_all(points), 3)
    assert [p.payload["content"] for p in result] == [
        "high-0", "high-1", "high-2",
    ]


def test_partial_tie_group_is_still_omitted():
    """Only the all-tied case should be rescued; partial ties keep old behaviour."""
    points = [_point(2.0, "high")] + [
        _point(1.0, f"tie-{i}") for i in range(50)
    ]
    result = bounded_keyword_ties(_fetch_all(points), 5)
    assert [p.payload["content"] for p in result] == ["high"]


def test_non_positive_top_k_returns_empty():
    points = [_point(1.0, f"memory-{i}") for i in range(10)]
    assert bounded_keyword_ties(_fetch_all(points), 0) == []
