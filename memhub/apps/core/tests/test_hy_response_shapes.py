"""HY response shape selection only; no provider, write routing or model calls."""

import asyncio
from collections.abc import Mapping
from copy import deepcopy

import pytest

from one_memory.hy_memory_bridge import HyMemoryBridgeError, _replied_memories
from tests.test_hy_coding_reply import CodingSceneClient, _bridge

ERROR = "hy_memory_search_shape_unknown"
CODING = {"memory_id": "synthetic-coding", "task": "synthetic task", "solution": "proposal"}
CHAT = {"memory_id": "synthetic-chat", "content": "synthetic chat", "score": 0.8}


def groups():
    return {"schema": [], "proactive": [], "normal": []}


@pytest.mark.parametrize("scene", ["productivity", "working"])
@pytest.mark.parametrize("chat_has_rows", [False, True])
def test_explicit_coding_scene_selects_coding_even_with_chat_groups(scene, chat_has_rows):
    chat = groups()
    if chat_has_rows:
        chat["normal"] = [CHAT]
    reply = {"scene": scene, "coding_memories": [CODING], "memories": chat}
    before = deepcopy(reply)
    result = _replied_memories(reply, ERROR)
    assert result == (CODING,)
    assert result[0] is CODING
    assert reply == before


@pytest.mark.parametrize("scene", ["normal", "chat"])
@pytest.mark.parametrize("grouped", [False, True])
def test_explicit_chat_scene_never_merges_other_channel(scene, grouped):
    chat = {**groups(), "normal": [CHAT]} if grouped else [CHAT]
    reply = {"scene": scene, "memories": chat, "coding_memories": [CODING]}
    assert _replied_memories(reply, ERROR) == (CHAT,)


@pytest.mark.parametrize(
    "reply,expected",
    [
        ({"memories": [CHAT]}, (CHAT,)),
        ({"memories": []}, ()),
        ({"memories": groups()}, ()),
        ({"coding_memories": [CODING]}, (CODING,)),
        ({"coding_memories": []}, ()),
        ({"scene": "productivity", "coding_memories": []}, ()),
        ({"scene": "normal", "memories": []}, ()),
        ({"scene": "productivity", "coding_memories": [], "memories": [CHAT]}, ()),
        ({"scene": "normal", "memories": [], "coding_memories": [CODING]}, ()),
    ],
)
def test_legacy_single_channel_and_selected_empty_shapes_remain_valid(reply, expected):
    assert _replied_memories(reply, ERROR) == expected


def test_all_chat_groups_preserve_rows_and_order_without_reinterpreting_metadata():
    schema = {"id": "schema", "score": 0.3}
    proactive = {"id": "proactive", "score": 0.2}
    reply = {
        "memories": {"schema": [schema], "proactive": [proactive], "normal": [CHAT]},
        "rewrite": {"used": False, "scene": "normal"},
        "elapsed_ms": 1,
    }
    assert _replied_memories(reply, ERROR) == (schema, proactive, CHAT)


@pytest.mark.parametrize(
    "scene", [None, False, 1, [], {}, "", "other", "coding", "NORMAL", " chat "]
)
def test_unknown_or_malformed_explicit_scene_never_falls_back(scene):
    with pytest.raises(HyMemoryBridgeError, match=f"^{ERROR}$"):
        _replied_memories({"scene": scene, "memories": [CHAT]}, ERROR)


@pytest.mark.parametrize(
    "reply",
    [
        {"scene": "productivity", "memories": [CHAT]},
        {"scene": "working", "memories": groups()},
        {"scene": "normal", "coding_memories": [CODING]},
        {"scene": "chat", "coding_memories": []},
        {"memories": [], "coding_memories": [CODING]},
        {"memories": [CHAT], "coding_memories": []},
        {"memories": groups(), "coding_memories": []},
        {"memories": [CHAT], "coding_memories": [CODING]},
    ],
)
def test_missing_selected_channel_or_ambiguous_unlabelled_channels_are_rejected(reply):
    with pytest.raises(HyMemoryBridgeError, match=f"^{ERROR}$"):
        _replied_memories(reply, ERROR)


@pytest.mark.parametrize(
    "reply",
    [
        None,
        [],
        {},
        {"memories": None},
        {"memories": {}},
        {"memories": {"normal": [CHAT]}},
        {"memories": {**groups(), "unexpected": [CHAT]}},
        {"memories": {**groups(), "schema": None}},
        {"memories": {**groups(), "normal": [CHAT, 1]}},
        {"memories": [CHAT, None]},
        {"memories": [1]},
        {"memories": (CHAT,)},
        {"coding_memories": {"normal": [CODING]}},
        {"coding_memories": [CODING, False]},
        {"scene": "productivity", "coding_memories": [1], "memories": groups()},
        {"scene": "productivity", "coding_memories": [CODING], "memories": {"bad": []}},
        {"scene": "normal", "memories": [CHAT], "coding_memories": None},
    ],
)
def test_malformed_channel_or_row_fails_whole_reply_instead_of_losing_rows(reply):
    with pytest.raises(HyMemoryBridgeError, match=f"^{ERROR}$"):
        _replied_memories(reply, ERROR)


def test_errors_use_supplied_safe_code_without_echoing_payload():
    secret_marker = "synthetic-private-payload-must-not-appear"
    error_code = "hy_memory_list_shape_unknown"
    with pytest.raises(HyMemoryBridgeError) as raised:
        _replied_memories({"memories": [CHAT, secret_marker]}, error_code)
    assert str(raised.value) == error_code
    assert secret_marker not in repr(raised.value)


def test_documented_dual_reply_reaches_existing_search_and_listing_consumers(tmp_path):
    row = {**CODING, "agent_id": "synthetic-workspace"}
    reply: Mapping[str, object] = {
        "scene": "productivity",
        "coding_memories": [row],
        "memories": groups(),
    }

    class Client(CodingSceneClient):
        def __init__(self):
            super().__init__(search_reply=reply, list_reply=reply)
            self.search_args = []

        def search(self, query, **kwargs):
            self.search_args.append(kwargs)
            return self.search_reply

    async def scenario():
        client = Client()
        bridge = _bridge(client, tmp_path)
        try:
            assert await bridge.search_ids("synthetic query", user_id="local", agent_id="*") == (
                "synthetic-coding",
            )
            assert await bridge.list_agent_ids(user_id="local") == ("synthetic-workspace",)
            assert client.search_args[0]["scene"] == "normal"
            assert client.search_args[0]["reader"] == "hybrid"
        finally:
            await bridge.close()

    asyncio.run(scenario())
