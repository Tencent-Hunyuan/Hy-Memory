"""Regression coverage for the public V1 metadata compatibility API."""

import json
from dataclasses import fields
from datetime import datetime

from hy_memory import MemoryMetadata


def test_metadata_to_dict_serializes_declared_fields():
    metadata = MemoryMetadata(uid="user", tags=["project"])

    payload = metadata.to_dict()

    assert set(payload) == {field.name for field in fields(MemoryMetadata)}
    assert json.loads(json.dumps(payload))["tags"] == ["project"]


def test_metadata_legacy_snapshot_roundtrip():
    timestamp = int(datetime(2026, 1, 1).timestamp())
    snapshot = {
        "uid": "user",
        "agent_id": "assistant",
        "layer": "l2_fact",
        "event_time": timestamp,
        "created_at": timestamp,
        "updated_at": timestamp,
        "tags": ["project"],
        "custom": {"project": "demo"},
    }

    restored = MemoryMetadata.from_dict(snapshot)
    payload = restored.to_dict()

    assert all(payload[key] == value for key, value in snapshot.items())
    assert MemoryMetadata.from_dict(payload) == restored
