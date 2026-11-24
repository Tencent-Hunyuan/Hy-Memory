"""Regression tests for the deterministic tag-index point id.

``_tag_point_id`` is the primary key of the per-user tag embedding index.  A
collision makes one user's tag vector overwrite another user's, and makes a
delete for one pair remove the other pair's point.
"""

import uuid

from hy_memory.data.vector_store_base import VectorStoreBase


_TAG_POINT_ID = VectorStoreBase._tag_point_id


def test_delimiter_in_either_field_does_not_create_a_collision():
    """The old ``tag_index::{user_id}::{tag}`` join was ambiguous."""
    pairs = [
        (("a::b", "c"), ("a", "b::c")),
        (("user", "tag::x"), ("user::tag", "x")),
        (("u::1", "t::2"), ("u", "1::t::2")),
        (("a::b::c", "d"), ("a::b", "c::d")),
    ]
    for left, right in pairs:
        assert _TAG_POINT_ID(*left) != _TAG_POINT_ID(*right), (left, right)


def test_distinct_pairs_with_delimiters_stay_distinct():
    """Every (user_id, tag) with an embedded separator keeps its own point."""
    ids = [
        _TAG_POINT_ID(user_id, tag)
        for user_id in ("a", "a::b", "a::b::c")
        for tag in ("x", "x::y", "x::y::z")
    ]
    assert len(set(ids)) == len(ids)


def test_id_is_still_deterministic_and_a_valid_uuid():
    first = _TAG_POINT_ID("a::b", "c::d")
    assert first == _TAG_POINT_ID("a::b", "c::d")
    assert str(uuid.UUID(first)) == first


def test_delimiter_free_pairs_keep_their_existing_point_id():
    """Existing tag points must not be orphaned by the collision fix."""
    for user_id, tag in (("alice", "python"), ("bob", "C++"), ("", "x")):
        expected = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"tag_index::{user_id}::{tag}")
        )
        assert _TAG_POINT_ID(user_id, tag) == expected
