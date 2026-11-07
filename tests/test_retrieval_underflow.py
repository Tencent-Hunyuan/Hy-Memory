"""Unit tests for _retrieval.underflow.fill_normal_slots.

The contract under test: ``limit`` bounds the normal channel only. Side
layers (L0/L6/L7) have their own quotas, so the returned list may be longer
than ``limit``. These tests pin that behaviour so a future "fix" does not
silently drop side-channel evidence.
"""

import math

import pytest

from hy_memory.pipelines._retrieval.underflow import (
    SIDE_LAYERS,
    fill_normal_slots,
    normal_count,
)


def _mem(mid, layer="l2_fact", score=1.0):
    return {"memory_id": mid, "layer": layer, "score": score}


def _candidates(n, layer="l2_fact", start=1.0, step=0.01):
    return [_mem(f"c{i}", layer=layer, score=start - i * step) for i in range(n)]


class TestNormalCount:
    def test_side_layers_are_not_counted(self):
        memories = [
            _mem("s1", "l0_basic_info"),
            _mem("s2", "l6_schema"),
            _mem("s3", "l7_intention"),
            _mem("n1", "l2_fact"),
            _mem("n2", "l3_summary"),
        ]
        assert normal_count(memories) == 2

    def test_side_layers_constant(self):
        assert SIDE_LAYERS == {"l0_basic_info", "l6_schema", "l7_intention"}


class TestFillNormalSlots:
    def test_side_layers_do_not_consume_limit(self):
        """limit=5 with 2 side entries still yields 5 normal results."""
        original = [_mem("s1", "l0_basic_info"), _mem("s2", "l7_intention")]
        out = fill_normal_slots(
            original, _candidates(6), limit=5, min_score=0.0,
        )
        assert normal_count(out) == 5
        # Side entries are preserved verbatim and are not counted as slots.
        assert [m["memory_id"] for m in out if m["layer"] in SIDE_LAYERS] == ["s1", "s2"]
        # Total may exceed limit: 5 normal + 2 side.
        assert len(out) == 7

    def test_normal_only_output_respects_limit(self):
        out = fill_normal_slots([], _candidates(6), limit=5, min_score=0.0)
        assert len(out) == 5
        assert [m["memory_id"] for m in out] == ["c0", "c1", "c2", "c3", "c4"]

    def test_existing_normals_are_preserved(self):
        original = [_mem("n1"), _mem("n2")]
        out = fill_normal_slots(original, _candidates(6), limit=5, min_score=0.0)
        assert [m["memory_id"] for m in out] == ["n1", "n2", "c0", "c1", "c2"]
        assert normal_count(out) == 5

    def test_duplicate_memory_ids_are_skipped(self):
        original = [_mem("c0")]
        out = fill_normal_slots(original, _candidates(3), limit=2, min_score=0.0)
        ids = [m["memory_id"] for m in out]
        assert ids == ["c0", "c1"]

    def test_candidate_side_layers_are_not_appended(self):
        original = [_mem("n1")]
        candidates = [_mem("s1", "l6_schema"), _mem("c0")]
        out = fill_normal_slots(original, candidates, limit=2, min_score=0.0)
        assert [m["memory_id"] for m in out] == ["n1", "c0"]

    def test_below_min_score_is_rejected(self):
        candidates = [_mem("low", score=0.2), _mem("ok", score=0.5)]
        out = fill_normal_slots([], candidates, limit=5, min_score=0.4)
        assert [m["memory_id"] for m in out] == ["ok"]

    @pytest.mark.parametrize("bad_score", [True, False, "0.9", None, math.nan, math.inf])
    def test_non_finite_or_non_numeric_scores_are_rejected(self, bad_score):
        candidates = [_mem("bad", score=bad_score), _mem("ok", score=0.5)]
        out = fill_normal_slots([], candidates, limit=5, min_score=0.0)
        assert [m["memory_id"] for m in out] == ["ok"]

    def test_empty_memory_id_is_rejected(self):
        candidates = [{"memory_id": "", "layer": "l2_fact", "score": 1.0}, _mem("ok")]
        out = fill_normal_slots([], candidates, limit=5, min_score=0.0)
        assert [m["memory_id"] for m in out] == ["ok"]

    def test_extras_are_inserted_before_first_side_entry(self):
        original = [
            _mem("n1"),
            _mem("s1", "l0_basic_info"),
            _mem("n2"),
        ]
        out = fill_normal_slots(original, _candidates(3), limit=3, min_score=0.0)
        assert [m["memory_id"] for m in out] == ["n1", "c0", "s1", "n2"]

    def test_full_normal_channel_returns_original_unchanged(self):
        original = [_mem("n1"), _mem("n2")]
        out = fill_normal_slots(original, _candidates(3), limit=2, min_score=0.0)
        assert out == original

    def test_zero_remaining_keeps_original_object_order(self):
        original = [_mem("s1", "l7_intention")]
        out = fill_normal_slots(original, _candidates(3), limit=0, min_score=0.0)
        assert out == original

    def test_does_not_mutate_inputs(self):
        original = [_mem("n1")]
        candidates = _candidates(3)
        original_snapshot = [dict(m) for m in original]
        candidates_snapshot = [dict(m) for m in candidates]
        fill_normal_slots(original, candidates, limit=3, min_score=0.0)
        assert original == original_snapshot
        assert candidates == candidates_snapshot
