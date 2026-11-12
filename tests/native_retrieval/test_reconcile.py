import asyncio
from datetime import datetime

import pytest

from hy_memory.agent.reconciler import MemoryReconciler
from conftest import CaptureLLM, FixedEmbed, node

pytestmark = pytest.mark.integration


def reconciler(store, llm=None):
    store.config.recall.reconcile_retriever = "native"
    return MemoryReconciler(store.config, llm or CaptureLLM())


async def test_T01_T02_actual_reconcile_prompt_escapes_dense_pool(server_store):
    store = server_store
    content_target = node("contentonlymarker", embedding=[0.0, 1.0])
    tag_target = node("tagged fact", tags=["tagonlymarker"], embedding=[0.0, 1.0])
    await store.upsert_batch(
        [node(f"noise {i}") for i in range(35)] + [content_target, tag_target]
    )
    llm = CaptureLLM()
    result = await reconciler(store, llm).reconcile(
        ["contentonlymarker", "tagonlymarker"], "alice", "a", store, FixedEmbed()
    )
    assert result.success
    assert len(llm.prompts) == 1
    assert (
        content_target.node_id in llm.prompts[0]
        and tag_target.node_id in llm.prompts[0]
    )


async def test_T12_necessary_failure_no_operations_or_mutations(populated, monkeypatch):
    store, _ = populated
    before, _ = store._client.scroll(store._collection_name, limit=100)
    llm = CaptureLLM()

    async def fail(*args, **kwargs):
        raise TimeoutError("required query timed out")

    monkeypatch.setattr(store, "native_query", fail)
    result = await reconciler(store, llm).reconcile(
        ["reconnectmarker"], "alice", "a", store, FixedEmbed()
    )
    assert not result.success and result.ops == [] and not llm.prompts
    after, _ = store._client.scroll(store._collection_name, limit=100)
    assert before == after


async def test_T12_short_embedding_is_failure(server_store):
    class ShortEmbed:
        async def embed_batch(self, texts):
            return [[1.0, 0.0]]

    result = await reconciler(server_store).reconcile(
        ["first", "second"], "alice", "a", server_store, ShortEmbed()
    )
    assert not result.success and result.ops == []


async def test_T13_backend_order_is_actual_prompt_order(server_store):
    store = server_store
    newest = node("orderword", memory_at=datetime(2026, 11, 10))
    oldest = node("unrelated", memory_at=datetime(2026, 11, 1))
    await store.upsert_batch([newest, oldest])
    llm = CaptureLLM()
    # Inspect the exact production backend plan and require a contrary time order.
    from hy_memory.data.vector_store_base import NativeQuery
    from hy_memory.models.memory import MemoryLayer

    plan = NativeQuery(
        texts=["orderword"],
        embeddings=[[1.0, 0.0]],
        user_ids=["alice"],
        layers=[MemoryLayer.L2_FACT, MemoryLayer.L4_IDENTITY],
        limit=60,
        include_sources=["default"],
    )
    hits = await store.native_query(plan)
    assert [h["node_id"] for h in hits] == [newest.node_id, oldest.node_id]
    result = await reconciler(store, llm).reconcile(
        ["orderword"], "alice", "a", store, FixedEmbed()
    )
    assert result.success
    assert llm.prompts[0].index(newest.node_id) < llm.prompts[0].index(oldest.node_id)


async def test_T14_healthy_empty_allows_add(server_store):
    llm = CaptureLLM()
    result = await reconciler(server_store, llm).reconcile(
        ["new fact"], "alice", "a", server_store, FixedEmbed()
    )
    assert result.success and len(result.ops) == 1 and result.ops[0].op == "ADD"
    assert not llm.prompts


async def test_T15_cosine_evidence_both_directions(server_store):
    store = server_store
    lexical = node("semanticword", tags=["semanticword"], embedding=[0.0, 1.0])
    dense = node("ordinary", embedding=[1.0, 0.0])
    await store.upsert_batch([lexical, dense])
    result = await reconciler(store).reconcile(
        ["semanticword"], "alice", "a", store, FixedEmbed()
    )
    assert result.success
    scores = dict(result.candidate_scores["semanticword"])
    assert scores[lexical.node_id] == pytest.approx(0.0)
    assert scores[dense.node_id] == pytest.approx(1.0)


async def test_T16_optional_cosine_failure_keeps_typed_relation(
    server_store, monkeypatch
):
    store = server_store
    target = node("relationword")
    await store.upsert(target)
    llm = CaptureLLM(
        [
            {
                "op": "ADD",
                "content": "relationword",
                "layer": "L2_FACT",
                "related": [{"target_id": target.node_id, "relation_type": "causes"}],
            }
        ]
    )

    async def fail(*args, **kwargs):
        raise TimeoutError("optional cosine evidence")

    monkeypatch.setattr(store, "native_cosine", fail)
    result = await reconciler(store, llm).reconcile(
        ["relationword"], "alice", "a", store, FixedEmbed()
    )
    assert result.success and result.ops[0].op == "ADD"
    assert result.ops[0].related == [
        {"target_id": target.node_id, "relation_type": "causes"}
    ]
    assert result.candidate_scores["relationword"] == []


async def test_T12_cancellation_propagates_before_llm(server_store, monkeypatch):
    llm = CaptureLLM()

    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(server_store, "native_query", cancel)
    with pytest.raises(asyncio.CancelledError):
        await reconciler(server_store, llm).reconcile(
            ["query"], "alice", "a", server_store, FixedEmbed()
        )
    assert not llm.prompts


@pytest.mark.parametrize("cosine_fails", [False, True])
async def test_T15_T16_writer_stores_and_projects_real_cosine(
    server_store, monkeypatch, cosine_fails
):
    from hy_memory.pipelines.writer import MemoryWriter
    from hy_memory.pipelines.base import WriteRequest

    class Graph:
        def __init__(self):
            self.nodes, self.edges = [], []

        async def upsert_memory_node(self, n):
            self.nodes.append(n)
            return n.node_id

        async def add_edge(self, src, dst, edge_type, properties=None):
            self.edges.append(
                (src, dst, properties["relation_type"], properties["weight"])
            )
            return True

    store = server_store
    lexical = node("semanticword", tags=["semanticword"], embedding=[0.0, 1.0])
    dense = node("ordinary", embedding=[1.0, 0.0])
    await store.upsert_batch([lexical, dense])
    llm = CaptureLLM(
        [
            {
                "op": "ADD",
                "content": "semanticword",
                "layer": "L2_FACT",
                "related": [{"target_id": lexical.node_id, "relation_type": "causes"}],
            }
        ]
    )
    graph = Graph()
    writer = MemoryWriter(
        store.config, embed_service=FixedEmbed(), vector_store=store, graph_store=graph
    )
    writer._vector_store, writer._vector_store_initialized = store, True
    writer._reconciler = reconciler(store, llm)
    if cosine_fails:

        async def fail(*args, **kwargs):
            raise TimeoutError("optional cosine")

        monkeypatch.setattr(store, "native_cosine", fail)
    request = WriteRequest(
        user_id="alice",
        agent_id="a",
        session_id="s",
        content="semanticword",
        request_id="native-graph-test",
    )
    stored, error, _, _ = await writer._reconcile_and_store(
        ["semanticword"],
        [{"content": "semanticword", "layer": "l2_fact"}],
        request,
        store,
        request.request_id,
    )
    assert error is None and len(stored) == 1
    assert (await store.native_get_by_ids(stored))[0].content == "semanticword"
    task = getattr(request, "_graph_projection_task", None)
    if task is not None:
        await task
    assert graph.nodes and (stored[0], lexical.node_id, "causes", 1.0) in graph.edges
    semantic_targets = {
        dst for _, dst, kind, _ in graph.edges if kind == "semantic_neighbor"
    }
    assert semantic_targets == (set() if cosine_fails else {dense.node_id})
