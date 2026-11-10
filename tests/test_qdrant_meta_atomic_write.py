"""The vector-store metadata must never be left truncated."""

from __future__ import annotations

from pathlib import Path

import pytest

from hy_memory.data.vector_store_qdrant import _publish_json


def test_publish_json_leaves_the_target_intact_when_the_swap_fails(tmp_path, monkeypatch):
    """meta.json is read by Qdrant when a collection is opened.

    ``_patch_local_meta`` rewrites it in place, so a crash or a full disk in
    the middle of that write leaves it truncated and the collection
    unreadable.  Publishing through a sibling file keeps the previous content
    until the swap succeeds.
    """
    target = tmp_path / "meta.json"
    target.write_text('{"original": true}', encoding="utf-8")

    def boom(_self, _target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "replace", boom)

    with pytest.raises(OSError, match="No space left on device"):
        _publish_json(target, {"changed": True})

    assert target.read_text(encoding="utf-8") == '{"original": true}'
    # The staging file must not be left behind.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["meta.json"]


def test_publish_json_writes_the_payload(tmp_path):
    target = tmp_path / "meta.json"
    target.write_text('{"original": true}', encoding="utf-8")

    _publish_json(target, {"collections": {"c": {"vectors": {}}}})

    assert '"collections"' in target.read_text(encoding="utf-8")
