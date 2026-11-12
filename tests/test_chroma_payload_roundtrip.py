# -*- coding: utf-8 -*-
"""
Chroma metadata 必须能完整往返 payload 里的 list/dict 字段。

Chroma 只接受扁平标量 metadata，所以 `_payload_to_chroma_metadata` 把 list/dict
都 JSON 序列化成字符串；反序列化靠 `_chroma_metadata_to_payload` 里的白名单。
白名单漏字段时，读回来的就是 JSON 字符串而不是 list，`MemoryNode.from_dict`
再 `list(...)` 一次就变成字符列表 —— 每次读写往返都会膨胀（见 issue #299）。
"""

from hy_memory.data.vector_store_base import VectorStoreBase
from hy_memory.data.vector_store_chroma import ChromaVectorStore
from hy_memory.models.memory import MemoryNode, MetaCognitionTag


def _roundtrip(node: MemoryNode) -> MemoryNode:
    """payload -> chroma metadata -> payload -> node"""
    payload = VectorStoreBase._node_to_payload(node)
    meta = ChromaVectorStore._payload_to_chroma_metadata(payload)
    return VectorStoreBase._payload_to_node(
        ChromaVectorStore._chroma_metadata_to_payload(meta)
    )


def test_tag_ners_survives_repeated_roundtrips():
    """issue #299 的最小复现：5 次往返后 tag_ners 必须保持原样。"""
    original_tags = ["topic"]
    original_ners = ["PERSON", ""]

    node = MemoryNode(
        content="synthetic",
        user_id="test_user",
        tags=list(original_tags),
        tag_ners=list(original_ners),
    )

    for roundtrip in range(5):
        node = _roundtrip(node)
        assert node.tags == original_tags, f"tags drifted after {roundtrip + 1} roundtrips"
        assert node.tag_ners == original_ners, (
            f"tag_ners drifted after {roundtrip + 1} roundtrips: {node.tag_ners!r}"
        )


def test_empty_tag_ners_stays_empty():
    """空 NER 列表不能变成 JSON 字符串的字符列表。"""
    node = MemoryNode(content="synthetic", user_id="test_user", tags=[], tag_ners=[])

    for _ in range(5):
        node = _roundtrip(node)
        assert node.tag_ners == []
        assert node.tags == []


def test_every_list_and_dict_payload_field_roundtrips():
    """通用护栏：payload 里任何 list/dict 字段都不能带回 JSON 字符串。

    新增这样的字段却忘了加进解码白名单时，这条会直接失败。
    """
    node = MemoryNode(
        content="synthetic",
        user_id="test_user",
        tags=["a", "b"],
        tag_ners=["PERSON", ""],
        meta_tags=[MetaCognitionTag.HIGH_CONFIDENCE, MetaCognitionTag.STALE],
        supersedes=["node-1"],
        superseded_by=["node-2"],
        custom={"key": "value"},
    )

    payload = VectorStoreBase._node_to_payload(node)
    meta = ChromaVectorStore._payload_to_chroma_metadata(payload)
    restored = ChromaVectorStore._chroma_metadata_to_payload(meta)

    structural = {
        key: value
        for key, value in payload.items()
        if isinstance(value, (list, dict))
    }
    assert structural, "payload 里应当存在 list/dict 字段"

    for key, value in structural.items():
        got = restored[key]
        assert not isinstance(got, str), (
            f"{key} 回来的是 JSON 字符串，解码白名单漏了这个字段: {got!r}"
        )
        assert isinstance(got, type(value)), (
            f"{key} 类型不对: expected {type(value).__name__}, got {type(got).__name__}"
        )
        assert got == value, f"{key} 内容不一致: expected {value!r}, got {got!r}"
