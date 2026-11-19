# -*- coding: utf-8 -*-
"""Regression: deduplicate() must not crash on None/non-string content.

The content hash is computed as hash(content.strip().lower()). When a dict
carries "content": None (a nullable DB column) or a non-string scalar, .strip()
raises AttributeError. Empty strings work, showing the code assumes str.
"""

from hy_memory.core.merger import Merger


def test_none_content_does_not_crash():
    result = Merger().deduplicate([{"id": "a", "content": None}])
    assert len(result) == 1


def test_non_string_content_does_not_crash():
    result = Merger().deduplicate([{"id": "a", "content": 123}])
    assert len(result) == 1


def test_empty_string_content_still_dedupes():
    result = Merger().deduplicate(
        [{"id": "a", "content": ""}, {"id": "b", "content": ""}]
    )
    assert len(result) == 1
