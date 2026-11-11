"""Real local Chroma lexical recall and its existing scope contract."""

import math
import uuid
from types import SimpleNamespace

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.data.vector_store_chroma import ChromaVectorStore
from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus


def make_node(content="ECONNRESET connection failure", **fields):
    node = MemoryNode(
        node_id=fields.pop("node_id", str(uuid.uuid4())),
        user_id=fields.pop("user_id", "u1"),
        agent_id=fields.pop("agent_id", "a1"),
        session_id=fields.pop("session_id", "s1"),
        layer=fields.pop("layer", MemoryLayer.L2_FACT),
        content=content,
        **fields,
    )
    node.embedding = [1.0, 0.0, 0.0]
    return node


def config_for(tmp_path, *, enabled=True, **fields):
    return MemoryConfig.from_dict({"vector_store": {
        "provider": "chroma",
        "collection_name": "test_keyword",
        "persist_directory": str(tmp_path / "chroma"),
        "embedding_dims": 3,
        "chroma_lexical_enabled": enabled,
        **fields,
    }})


@pytest.fixture
async def store(tmp_path):
    store = ChromaVectorStore(config_for(tmp_path))
    await store.initialize()
    yield store
    await store.close()


def ids(hits):
    return {hit["node_id"] for hit in hits}


@pytest.mark.asyncio
@pytest.mark.parametrize(("content", "query"), [
    ("修复ECONNRESET错误并重试连接", "ECONNRESET"),
    ("用户希望得到务实解决方案", "务实解决方案"),
    ("Deploy to production with twine", "deploy production"),
    ("Invoke loadUserProfile before rendering", "loadUserProfile"),
    ("Invoke foo_bar before rendering", "foo_bar"),
    ("Change src/foo_bar.py before deployment", "src/foo_bar.py"),
    ("Change src/foo_bar.py before deployment", "foo_bar.py"),
    ("The library requires C++", "C++"),
    ("Deploy version v1.2.3", "v1.2.3"),
])
async def test_lexical_query_recalls_current_content(store, content, query):
    target = make_node(content)
    unrelated = make_node("unrelated weather observation")
    await store.upsert_batch([target, unrelated])

    hits = await store.keyword_search(query, user_id="u1")

    assert target.node_id in ids(hits)
    assert unrelated.node_id not in ids(hits)
    assert store.keyword_score_normalized
    assert all(math.isfinite(hit["score"]) and 0 < hit["score"] <= 1 for hit in hits)
    assert all(hit["node"].embedding is None for hit in hits)


@pytest.mark.asyncio
async def test_raw_query_preserves_complete_symbol(store):
    target = make_node("Compiler requires C++")
    await store.upsert(target)
    hits = await store.keyword_search("c", raw_query="C++", user_id="u1")
    assert ids(hits) == {target.node_id}
    assert hits[0]["score"] >= 0.9
    assert await store.keyword_search("C++", raw_query="", user_id="u1") == []


@pytest.mark.asyncio
@pytest.mark.parametrize("query", ["", "   ", "!!!", '"', "(", "OR", "NEAR"])
async def test_empty_or_query_syntax_is_safe(store, query):
    await store.upsert(make_node())
    assert await store.keyword_search(query, user_id="u1") == []


@pytest.mark.asyncio
async def test_top_k_and_stable_ties(store):
    nodes = [make_node(node_id=f"n{i}") for i in range(8)]
    await store.upsert_batch(nodes)
    first = await store.keyword_search("ECONNRESET", user_id="u1", top_k=3)
    second = await store.keyword_search("ECONNRESET", user_id="u1", top_k=3)
    assert [hit["node_id"] for hit in first] == [hit["node_id"] for hit in second]
    assert len(first) == 3
    assert await store.keyword_search("ECONNRESET", top_k=0) == []


@pytest.mark.asyncio
async def test_weak_single_result_is_not_query_normalized_to_one(store):
    await store.upsert(make_node("ordinary common word"))
    hits = await store.keyword_search("common", user_id="u1")
    assert len(hits) == 1
    assert 0 < hits[0]["score"] < 0.4


@pytest.mark.asyncio
async def test_multiple_users_and_precise_isolation_precedence(store):
    one = make_node(user_id="u1", agent_id="a1", session_id="s1")
    two = make_node(user_id="u2", agent_id="a2", session_id="s2")
    three = make_node(user_id="u3", agent_id="a1", session_id="s3")
    await store.upsert_batch([one, two, three])

    hits = await store.keyword_search("ECONNRESET", user_id="u3", user_ids=["u1", "u2"])
    assert ids(hits) == {one.node_id, two.node_id}
    hits = await store.keyword_search(
        "ECONNRESET", user_ids=["u1", "u2"], agent_ids=["a2"],
    )
    assert ids(hits) == {two.node_id}
    hits = await store.keyword_search(
        "ECONNRESET", user_id="u3", agent_ids=["a1"],
        isolation_key=one.get_isolation_key(), isolation_keys=[two.get_isolation_key()],
    )
    assert ids(hits) == {two.node_id}
    hits = await store.keyword_search(
        "ECONNRESET", user_id="u3", isolation_key=one.get_isolation_key(),
    )
    assert ids(hits) == {one.node_id}


@pytest.mark.asyncio
@pytest.mark.parametrize("filters", [
    {"user_id": "u1"},
    {"user_ids": ["u1", "u2"], "agent_ids": ["a2"]},
    {"user_id": "u1", "layers": [MemoryLayer.L4_IDENTITY]},
    {"user_id": "u1", "status_filter": [MemoryStatus.SUPERSEDED], "only_latest": False},
    {"user_id": "u1", "only_latest": False},
    {"include_sources": ["allowed"], "exclude_sources": ["blocked"]},
    {"include_sources": ["allowed"], "exclude_sources": ["allowed"]},
])
async def test_dense_and_lexical_scopes_select_same_nodes(store, filters):
    nodes = [
        make_node(user_id="u1", agent_id="a1", source="allowed"),
        make_node(user_id="u2", agent_id="a2", source="allowed"),
        make_node(user_id="u1", layer=MemoryLayer.L4_IDENTITY, source="blocked"),
        make_node(user_id="u1", status=MemoryStatus.SUPERSEDED, is_latest=False),
        make_node(user_id="u1", status=MemoryStatus.SHADOW),
        make_node(user_id="u1", is_latest=False),
    ]
    await store.upsert_batch(nodes)
    lexical = await store.keyword_search("ECONNRESET", top_k=20, **filters)
    dense = await store.search([1.0, 0.0, 0.0], limit=20, **filters)
    assert ids(lexical) == ids(dense)


@pytest.mark.asyncio
async def test_source_filters_run_before_top_k(store):
    forbidden = [make_node(source="blocked", node_id=f"decoy-{i:03}") for i in range(85)]
    target = make_node(source="allowed", node_id="target")
    await store.upsert_batch(forbidden + [target])
    hits = await store.keyword_search(
        "ECONNRESET", user_id="u1", exclude_sources=["blocked"], top_k=1,
    )
    assert ids(hits) == {target.node_id}


@pytest.mark.asyncio
async def test_content_tags_source_and_status_updates_are_current(store):
    target = make_node("ECONNRESET is present", tags=["legacytag"], source="old")
    await store.upsert(target)
    assert ids(await store.keyword_search("ECONNRESET")) == {target.node_id}
    assert await store.update_payload(target.node_id, {
        "content": "ETIMEDOUT is present", "tags": ["currenttag"], "source": "new",
    })
    assert await store.keyword_search("ECONNRESET") == []
    assert await store.keyword_search("legacytag") == []
    assert ids(await store.keyword_search("ETIMEDOUT", include_sources=["new"])) == {target.node_id}
    assert ids(await store.keyword_search("currenttag")) == {target.node_id}
    assert await store.keyword_search("ETIMEDOUT", include_sources=["old"]) == []
    assert (await store.get_by_id(target.node_id)).embedding == target.embedding
    assert await store.update_payload(target.node_id, {"status": MemoryStatus.SHADOW.value})
    assert await store.keyword_search("ETIMEDOUT") == []


@pytest.mark.asyncio
async def test_embedding_and_access_updates_leave_lexical_result_intact(store):
    target = make_node()
    await store.upsert(target)
    before = await store.keyword_search("ECONNRESET")
    assert await store.update_embedding(target.node_id, [0.0, 1.0, 0.0])
    assert await store.update_payload(target.node_id, {"access_count": 7, "last_accessed_at": 1})
    after = await store.keyword_search("ECONNRESET")
    assert [(h["node_id"], h["score"]) for h in after] == [(h["node_id"], h["score"]) for h in before]
    assert after[0]["node"].access_count == 7


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["delete", "delete_by_isolation_key", "delete_by_metadata"])
async def test_delete_variants_remove_only_target_scope(store, method):
    target = make_node(user_id="u1")
    other = make_node(user_id="u2")
    await store.upsert_batch([target, other])
    if method == "delete":
        assert await store.delete(target.node_id)
    elif method == "delete_by_isolation_key":
        assert await store.delete_by_isolation_key(target.get_isolation_key()) == 1
    else:
        assert await store.delete_by_metadata("u1", "a1", "s1") == 1
    assert ids(await store.keyword_search("ECONNRESET")) == {other.node_id}


@pytest.mark.asyncio
async def test_rebuild_uses_current_content_and_preserves_non_uuid_node_id(store):
    target = make_node("newSymbol", node_id="non-uuid-sdk-node")
    await store.upsert(target)
    assert (await store.rebuild_lexical_index())["state"] == "ready"
    hits = await store.keyword_search("newSymbol")
    assert ids(hits) == {target.node_id}
    assert hits[0]["node"].node_id == target.node_id
    assert (await store.get_stats())["lexical_index"]["state"] == "ready"


@pytest.mark.asyncio
async def test_default_disabled_keeps_dense_recall(tmp_path):
    store = ChromaVectorStore(config_for(tmp_path, enabled=False))
    await store.initialize()
    try:
        target = make_node()
        await store.upsert(target)
        assert await store.keyword_search("ECONNRESET") == []
        assert ids(await store.search([1.0, 0.0, 0.0])) == {target.node_id}
        assert (await store.rebuild_lexical_index())["state"] == "disabled"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_remote_chroma_does_not_create_local_projection(tmp_path, monkeypatch):
    import chromadb

    collection = SimpleNamespace()
    client = SimpleNamespace(get_or_create_collection=lambda **kw: collection)
    monkeypatch.setattr(chromadb, "HttpClient", lambda **kw: client)
    store = ChromaVectorStore(config_for(tmp_path, host="remote.example"))
    await store.initialize()
    try:
        assert await store.keyword_search("ECONNRESET") == []
        assert (await store.rebuild_lexical_index())["reason"] == "remote_chroma"
        assert not (tmp_path / "chroma").exists()
    finally:
        await store.close()


def test_opt_in_config_environment_and_roundtrip(monkeypatch):
    monkeypatch.delenv("MEMORY_CHROMA_LEXICAL_ENABLED", raising=False)
    assert MemoryConfig().vector_store.chroma_lexical_enabled is False
    monkeypatch.setenv("MEMORY_CHROMA_LEXICAL_ENABLED", "true")
    config = MemoryConfig()
    assert config.vector_store.chroma_lexical_enabled is True
    assert MemoryConfig.from_dict(config.to_dict()).vector_store.chroma_lexical_enabled is True
    assert MemoryConfig.from_dict({"vector_store": {
        "chroma_lexical_enabled": False,
    }}).vector_store.chroma_lexical_enabled is False
