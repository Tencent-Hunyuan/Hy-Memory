import pytest

from hy_memory.data.vector_store_base import NativeQuery
from hy_memory.models.memory import MemoryLayer, MemoryNode
from hy_memory.retrieval import NativeRetrievalError
from conftest import node

pytestmark = pytest.mark.integration


def plan(text, **kwargs):
    return NativeQuery(
        texts=[text],
        embeddings=kwargs.pop("embeddings", []),
        user_ids=kwargs.pop("user_ids", ["alice"]),
        layers=[MemoryLayer.L2_FACT],
        mode=kwargs.pop("mode", "keyword"),
        **kwargs,
    )


async def test_T03_dual_tags_strict(server_store):
    from qdrant_client import models as m

    store = server_store
    compound, exact = node(tags=["foo bar"]), node(tags=["foo"])
    await store.upsert_batch([compound, exact])
    store._client.update_collection(
        store._collection_name,
        strict_mode_config=m.StrictModeConfig(
            enabled=True, unindexed_filtering_retrieve=False
        ),
    )
    records, _ = store._client.scroll(
        store._collection_name,
        scroll_filter=m.Filter(
            must=[m.FieldCondition(key="tags", match=m.MatchValue(value="foo"))]
        ),
    )
    assert [r.payload["node_id"] for r in records] == [exact.node_id]
    assert {hit["node_id"] for hit in await store.native_query(plan("foo"))} == {
        exact.node_id,
        compound.node_id,
    }


async def test_chinese_content_and_tags_native_tokenization(server_store):
    content = node("用户喜欢北京烤鸭")
    tagged = node("ordinary", tags=["用户喜欢北京烤鸭"])
    await server_store.upsert_batch([content, tagged])
    assert {h["node_id"] for h in await server_store.native_query(plan("烤鸭"))} == {
        content.node_id,
        tagged.node_id,
    }


async def test_T05_scope_intersection_and_sources(server_store):
    store = server_store
    alice, bob = node("scopeword"), node("scopeword", user_id="bob")
    outside = [node("scopeword", user_id="outside") for _ in range(40)]
    mixed = node("scopeword", source="mixed")
    cursor = node("scopeword", source="cursor")
    await store.upsert_batch([alice, bob, mixed, cursor] + outside)
    result = await store.native_query(
        plan("scopeword", user_ids=["alice", "bob"], exclude_sources=["cursor"])
    )
    assert {h["node_id"] for h in result} == {alice.node_id, bob.node_id}
    isolation = MemoryNode.build_isolation_key("bob", "a", "s")
    assert await store.native_query(plan("scopeword", isolation_keys=[isolation])) == []
    assert (
        await store.native_query(
            plan("scopeword", include_sources=["cursor"], exclude_sources=["cursor"])
        )
        == []
    )


async def test_T09_update_clear_and_delete(server_store):
    store = server_store
    target = node("oldcontentmarker", tags=["oldtagmarker"])
    await store.upsert(target)
    assert await store.native_query(plan("oldcontentmarker"))
    assert await store.update_payload(
        target.node_id, {"content": "newcontentmarker", "tags": ["newtagmarker"]}
    )
    assert not await store.native_query(plan("oldcontentmarker"))
    assert not await store.native_query(plan("oldtagmarker"))
    assert await store.native_query(plan("newcontentmarker"))
    assert await store.native_query(plan("newtagmarker"))
    assert await store.update_payload(target.node_id, {"content": "", "tags": []})
    assert not await store.native_query(plan("newtagmarker"))
    assert not await store.native_query(plan("newcontentmarker"))
    await store.update_payload(target.node_id, {"content": "deleteword"})
    assert await store.delete(target.node_id)
    assert not await store.native_query(plan("deleteword"))
    await store.upsert(node("rangedeleteword"))
    await store.delete_by_metadata("alice", agent_id="a", session_id="s")
    assert not await store.native_query(plan("rangedeleteword"))


async def test_T10_prepare_existing_idempotent_and_conflict(server_store, monkeypatch):
    store = server_store
    store._client.delete_payload_index(store._collection_name, "content", wait=True)
    target = node("beforeprepareword")
    await store.upsert(target)
    with pytest.raises(NativeRetrievalError, match="missing native") as failure:
        await store.native_query(plan("beforeprepareword"))
    assert failure.value.reason == "not_ready"

    def no_upsert(*args, **kwargs):
        raise AssertionError("prepare must not re-upsert")

    monkeypatch.setattr(store._client, "upsert", no_upsert)
    await store.prepare_native_retrieval()
    await store.prepare_native_retrieval()
    assert (await store.native_query(plan("beforeprepareword")))[0][
        "node_id"
    ] == target.node_id
    store._client.delete_payload_index(store._collection_name, "content", wait=True)
    store._client.create_payload_index(
        store._collection_name, "content", field_schema="keyword", wait=True
    )
    with pytest.raises(NativeRetrievalError, match="content") as failure:
        await store.prepare_native_retrieval()
    assert failure.value.reason == "schema_conflict"
    assert (
        store._client.get_collection(store._collection_name)
        .payload_schema["content"]
        .data_type
        == "keyword"
    )


async def test_T10_interrupted_prepare_retries_actual_schema(server_store, monkeypatch):
    store = server_store
    for field in ("content", "tags[]", "tags"):
        store._client.delete_payload_index(store._collection_name, field, wait=True)
    original = store._client.create_payload_index
    count = 0

    def interrupt(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise TimeoutError("interrupted")
        return original(*args, **kwargs)

    monkeypatch.setattr(store._client, "create_payload_index", interrupt)
    with pytest.raises(NativeRetrievalError):
        await store.prepare_native_retrieval()
    monkeypatch.setattr(store._client, "create_payload_index", original)
    await store.prepare_native_retrieval()
