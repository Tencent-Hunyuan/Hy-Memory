"""Regression coverage for the public IntentionNode serialization contract."""

import json

from hy_memory.models.memory import IntentionNode


def test_to_dict_carries_the_trigger_event_embedding():
    node = IntentionNode(
        node_id="intention-1",
        trigger_event_embedding=[0.1, 0.2, 0.3],
    )

    payload = node.to_dict()

    assert payload.get("trigger_event_embedding") == [0.1, 0.2, 0.3]


def test_trigger_event_embedding_is_json_serializable():
    node = IntentionNode(
        node_id="intention-1",
        trigger_event_embedding=[0.1, 0.2, 0.3],
    )

    encoded = json.dumps(node.to_dict())

    assert json.loads(encoded)["trigger_event_embedding"] == [0.1, 0.2, 0.3]


def test_trigger_event_embedding_survives_a_roundtrip():
    node = IntentionNode(
        node_id="intention-1",
        trigger_event_pattern="release cut",
        trigger_event_embedding=[0.1, 0.2, 0.3],
        intention_content="Remind me about the release",
    )

    restored = IntentionNode.from_dict(node.to_dict())

    assert restored.node_id == node.node_id
    assert restored.trigger_event_pattern == node.trigger_event_pattern
    assert restored.trigger_event_embedding == [0.1, 0.2, 0.3]
    assert restored.intention_content == node.intention_content
