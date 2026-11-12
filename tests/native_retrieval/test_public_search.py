import asyncio
from datetime import datetime

import pytest

from hy_memory.retrieval import NATIVE_CONFIG_ID as CONFIG_ID
from hy_memory.models.memory import MemoryStatus
from hy_memory.retrieval import NativeRetrievalError, NativeScoreThreshold
from conftest import node, public_client

pytestmark = pytest.mark.integration


async def test_T01_dense_pool_escape_public(populated):
    store, target = populated
    client = public_client(store)
    dense = await client.async_search(
        "reconnectmarker",
        reader="native",
        native_mode="dense",
        user_ids=["alice"],
        limit=10,
    )
    assert target.node_id not in {m["memory_id"] for m in dense["memories"]["normal"]}
    # Prove the full dense top-30 excludes the target, independently of public limit.
    from hy_memory.data.vector_store_base import NativeQuery
    from hy_memory.models.memory import MemoryLayer

    raw_dense = await store.native_query(
        NativeQuery(
            texts=["reconnectmarker"],
            embeddings=[[1.0, 0.0]],
            user_ids=["alice"],
            layers=[MemoryLayer.L2_FACT],
            mode="dense",
            limit=30,
        )
    )
    assert len(raw_dense) == 30
    assert target.node_id not in {h["node_id"] for h in raw_dense}
    for mode in ("keyword", "hybrid"):
        result = await client.async_search(
            "reconnectmarker", reader="native", native_mode=mode, user_ids=["alice"]
        )
        assert target.node_id in {m["memory_id"] for m in result["memories"]["normal"]}


async def test_T02_tags_only_public(server_store):
    target = node(tags=["tagonlymarker"], embedding=[0.0, 1.0])
    await server_store.upsert_batch(
        [node(f"distractor {i}") for i in range(35)] + [target]
    )
    result = await public_client(server_store).async_search(
        "tagonlymarker", reader="native", user_ids=["alice"]
    )
    assert target.node_id in {m["memory_id"] for m in result["memories"]["normal"]}


async def test_T04_raw_lexical_query_survives_rewrite_no_keyword_embedding(
    populated, monkeypatch
):
    store, target = populated

    class NoEmbed:
        async def embed(self, *args, **kwargs):
            raise AssertionError("keyword must not embed")

    client = public_client(store, NoEmbed())

    async def rewrite(*args, **kwargs):
        return dict(
            ok=True, scene="chat", rewrite_query="network problem", created_after=None
        )

    monkeypatch.setattr("hy_memory.coding.judge.analyze_queries", rewrite)
    result = await client.async_search(
        queries=["context", "ECONNRESET"],
        search_mode="agentic",
        reader="native",
        native_mode="keyword",
        user_ids=["alice"],
    )
    assert result["rewrite"]["effective_query"] == "network problem"
    assert result["memories"]["normal"][0]["memory_id"] == target.node_id


async def test_T06_cross_agent_head_and_T07_final_time(server_store):
    store = server_store
    head = node("current state", agent_id="b", memory_at=datetime(2026, 11, 10))
    old = node(
        "historicalmarker",
        memory_at=datetime(2026, 11, 1),
        status=MemoryStatus.SUPERSEDED,
        is_latest=False,
        superseded_by=[head.node_id],
    )
    head.supersedes = [old.node_id]
    await store.upsert_batch([head, old])
    client = public_client(store)
    opts = dict(
        reader="native", native_mode="keyword", user_ids=["alice"], agent_ids=["a"]
    )
    result = await client.async_search(
        "historicalmarker", created_after=datetime(2026, 11, 5).timestamp(), **opts
    )
    hit = result["memories"]["normal"][0]
    assert hit["memory_id"] == head.node_id and hit["matched_id"] == old.node_id
    assert hit["agent_id"] == "b"
    assert len(hit["evolution_chain"]) == 2
    assert not (
        await client.async_search(
            "historicalmarker", created_after=datetime(2026, 11, 11).timestamp(), **opts
        )
    )["memories"]["normal"]
    # Equality is included for time, unlike the score threshold contract.
    assert (
        await client.async_search(
            "historicalmarker", created_after=head.memory_at.timestamp(), **opts
        )
    )["memories"]["normal"]


async def test_T08_raw_score_exclusive_threshold(server_store):
    target = node("weakmarker")
    await server_store.upsert(target)
    client = public_client(server_store)
    opts = dict(reader="native", native_mode="keyword", user_ids=["alice"])
    hit = (await client.async_search("weakmarker", **opts))["memories"]["normal"][0]
    assert hit["score"] != 1.0
    assert hit["config_id"] == CONFIG_ID and hit["score_kind"] == "rrf"
    threshold = NativeScoreThreshold(hit["score"], "keyword", "rrf", CONFIG_ID)
    assert not (
        await client.async_search("weakmarker", native_threshold=threshold, **opts)
    )["memories"]["normal"]
    below = NativeScoreThreshold(hit["score"] - 1e-10, "keyword", "rrf", CONFIG_ID)
    same = (await client.async_search("weakmarker", native_threshold=below, **opts))[
        "memories"
    ]["normal"][0]
    assert same["score"] == hit["score"]
    with pytest.raises(NativeRetrievalError):
        await client.async_search(
            "weakmarker",
            native_threshold=NativeScoreThreshold(0, "hybrid", "rrf", CONFIG_ID),
            **opts,
        )


async def test_T11_public_search_is_read_only(populated, monkeypatch):
    store, _ = populated
    client = public_client(store)
    client._config.recall.strength_enabled = True

    def forbidden(*args, **kwargs):
        raise AssertionError("native read must not schedule storage writes")

    monkeypatch.setattr(client, "_spawn_search_dedup", forbidden)
    before, _ = store._client.scroll(
        store._collection_name, limit=100, with_vectors=True
    )
    tasks = set(asyncio.all_tasks())
    await client.async_search("reconnectmarker", reader="native", user_ids=["alice"])
    after, _ = store._client.scroll(
        store._collection_name, limit=100, with_vectors=True
    )
    assert before == after
    assert set(asyncio.all_tasks()) == tasks


async def test_T17_automatic_working_route_rejected(server_store, monkeypatch):
    client = public_client(server_store)

    async def working(*args, **kwargs):
        return dict(ok=True, scene="working", rewrite_query="work", created_after=None)

    monkeypatch.setattr("hy_memory.coding.judge.analyze_queries", working)
    with pytest.raises(NativeRetrievalError, match="cannot route"):
        await client.async_search(
            "work", search_mode="agentic", reader="native", user_ids=["alice"]
        )


async def test_sync_public_search_and_prepare(populated):
    from hy_memory.client import _LoopThread

    store, target = populated
    client = public_client(store)
    loop = _LoopThread()
    client._search_loop_thread = loop
    try:
        prepared = await asyncio.to_thread(client.prepare_native_retrieval)
        assert prepared["config_id"] == CONFIG_ID
        result = await asyncio.to_thread(
            client.search,
            "reconnectmarker",
            reader="native",
            native_mode="keyword",
            user_ids=["alice"],
        )
        assert result["memories"]["normal"][0]["memory_id"] == target.node_id
    finally:
        loop.stop()


async def test_T07_real_server_incomplete_cycle_and_multi_ancestor(server_store):
    store = server_store
    client = public_client(store)
    opts = dict(reader="native", native_mode="keyword", user_ids=["alice"])
    missing = node(
        "missingheadmarker",
        superseded_by=["missing-id"],
        is_latest=False,
        status=MemoryStatus.SUPERSEDED,
    )
    await store.upsert(missing)
    with pytest.raises(NativeRetrievalError) as failure:
        await client.async_search("missingheadmarker", **opts)
    assert failure.value.reason == "integrity"
    first, second = node("cyclemarker"), node("cyclemarker")
    first.superseded_by, second.superseded_by = [second.node_id], [first.node_id]
    await store.upsert_batch([first, second])
    with pytest.raises(NativeRetrievalError) as failure:
        await client.async_search("cyclemarker", **opts)
    assert failure.value.reason == "cycle"
    head = node("merged state")
    ancestors = [
        node(
            "multiancestormarker",
            superseded_by=[head.node_id],
            is_latest=False,
            status=MemoryStatus.SUPERSEDED,
        )
        for _ in range(2)
    ]
    head.supersedes = [n.node_id for n in ancestors]
    await store.upsert_batch([head] + ancestors)
    result = await client.async_search("multiancestormarker", **opts)
    assert [m["memory_id"] for m in result["memories"]["normal"]] == [head.node_id]
