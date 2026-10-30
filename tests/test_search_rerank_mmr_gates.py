# -*- coding: utf-8 -*-
"""search(rerank=...) 与 MMR 的按次开关。

- rerank=False：本地 cross-encoder 与图重排都不跑（看 spy 是否被调到）。
- MMR：调用方的 limit < MMR_MAX_TOP_K 时启用，>= 时跳过。
- 默认值（rerank=True、limit=10）保持原行为。
"""

import asyncio
import math
import uuid

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus
from hy_memory.pipelines import reader_base
from hy_memory.pipelines._retrieval.diversity import (
    MMR_MAX_TOP_K,
    mmr_applies,
    near_dup_applies,
)
from hy_memory.pipelines.base import ReadRequest

DIMS = 32


def _unit(vec):
    n = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / n for x in vec]


def _query_vec():
    return _unit([1.0] + [0.0] * (DIMS - 1))


def _vec(cos):
    return _unit([cos, math.sqrt(1 - cos * cos)] + [0.0] * (DIMS - 2))


@pytest.fixture
def config(tmp_path):
    return MemoryConfig.from_dict({"vector_store": {
        "provider": "chroma",
        "collection_name": f"t_gates_{uuid.uuid4().hex[:8]}",
        "persist_directory": str(tmp_path / "chroma"),
        "embedding_dims": DIMS,
    }})


async def _reader_with(config, count):
    from hy_memory.data.vector_store_chroma import ChromaVectorStore
    from hy_memory.pipelines.reader_legacy import LegacyReadPipeline

    store = ChromaVectorStore(config)
    await store.initialize()
    for index in range(count):
        node = MemoryNode(
            node_id=f"m{index:02d}", user_id="u1", agent_id="a1", session_id="s1",
            content=f"memory number {index} about topic{index} and detail{index}",
            layer=MemoryLayer.L2_FACT, status=MemoryStatus.ACTIVE, is_latest=True,
        )
        node.embedding = _vec(0.95 - index * 0.01)
        await store.upsert(node)
    reader = LegacyReadPipeline(config, vector_store=store)
    await reader.initialize()
    return reader, store


def _request(limit, **kw):
    return ReadRequest(
        query="memory topic", query_embedding=_query_vec(),
        user_ids=["u1"], agent_ids=["a1"],
        limit=limit, min_score=0.2, schema_min_score=0.2, **kw,
    )


@pytest.fixture
def mmr_spy(monkeypatch):
    calls = []
    real = reader_base.mmr_select

    def _spy(hits, limit, **kw):
        calls.append(limit)
        return real(hits, limit, **kw)

    monkeypatch.setattr(reader_base, "mmr_select", _spy)
    return calls


def test_mmr_threshold_is_defined_once():
    assert MMR_MAX_TOP_K == 20
    assert mmr_applies(19, 0.55)
    assert not mmr_applies(20, 0.55)
    # 权重仍由 MMR_LAMBDA 控制：关掉 λ 时任何 top-k 都不走 MMR
    assert not mmr_applies(5, 0.0)
    assert not mmr_applies(5, 1.0)


def test_mmr_runs_when_limit_below_threshold(config, mmr_spy):
    async def _run():
        reader, store = await _reader_with(config, 12)
        try:
            resp = await reader.read(_request(5))
            assert resp.success
            assert resp.extra["config"]["mmr_applied"] is True
            assert len(resp.memories) == 5
        finally:
            await store.close()

    asyncio.run(_run())
    assert mmr_spy == [5]


def test_mmr_skipped_when_limit_reaches_threshold(config, mmr_spy):
    async def _run():
        reader, store = await _reader_with(config, MMR_MAX_TOP_K + 6)
        try:
            resp = await reader.read(_request(MMR_MAX_TOP_K))
            assert resp.success
            assert resp.extra["config"]["mmr_applied"] is False
            ids = [m["memory_id"] for m in resp.memories]
            assert len(ids) == MMR_MAX_TOP_K
            # 无 MMR：按召回分（构造时单调递减）原序截断
            assert ids == [f"m{index:02d}" for index in range(MMR_MAX_TOP_K)]
        finally:
            await store.close()

    asyncio.run(_run())
    assert mmr_spy == []


@pytest.fixture
def near_dup_spy(monkeypatch):
    calls = []
    real = reader_base.near_dup_demote

    def _spy(hits, **kw):
        calls.append(len(hits))
        return real(hits, **kw)

    monkeypatch.setattr(reader_base, "near_dup_demote", _spy)
    return calls


def test_near_dup_shares_the_mmr_threshold():
    # Pairwise Jaccard against every kept candidate: quadratic in the pool,
    # which grows with limit. Same gate as MMR.
    assert near_dup_applies(MMR_MAX_TOP_K - 1)
    assert not near_dup_applies(MMR_MAX_TOP_K)


def test_near_dup_runs_when_limit_below_threshold(config, near_dup_spy):
    async def _run():
        reader, store = await _reader_with(config, 12)
        try:
            resp = await reader.read(_request(5))
            assert resp.extra["config"]["near_dup_applied"] is True
        finally:
            await store.close()

    asyncio.run(_run())
    assert len(near_dup_spy) == 1


def test_near_dup_skipped_when_limit_reaches_threshold(config, near_dup_spy):
    async def _run():
        reader, store = await _reader_with(config, MMR_MAX_TOP_K + 6)
        try:
            resp = await reader.read(_request(MMR_MAX_TOP_K))
            assert resp.success
            assert resp.extra["config"]["near_dup_applied"] is False
        finally:
            await store.close()

    asyncio.run(_run())
    assert near_dup_spy == []


class _FakeReranker:
    model_name = "fake-cross-encoder"
    device = "cpu"
    load_error = ""

    def __init__(self):
        self.calls = 0

    def score(self, query, documents):
        self.calls += 1
        # 反转召回序：分数与原名次相反
        return [float(i) for i in range(len(documents))]


@pytest.fixture
def rerank_spies(config, monkeypatch):
    config.recall.local_rerank_enabled = True
    reranker = _FakeReranker()
    factory_calls = []

    def _get_local_reranker(*args, **kwargs):
        factory_calls.append(kwargs)
        return reranker

    monkeypatch.setattr(reader_base, "get_local_reranker", _get_local_reranker)
    graph_calls = []

    async def _graph_rerank(self, *, hits, **kwargs):
        graph_calls.append(len(hits))
        return hits, [], {}

    monkeypatch.setattr(
        reader_base.BaseReadPipeline, "_apply_graph_rerank", _graph_rerank,
    )
    monkeypatch.setattr(
        reader_base.BaseReadPipeline, "_expansion_on", lambda self: True,
    )
    return {"reranker": reranker, "factory": factory_calls, "graph": graph_calls}


def test_rerank_false_skips_local_and_graph_rerank(config, rerank_spies):
    async def _run():
        reader, store = await _reader_with(config, 8)
        try:
            resp = await reader.read(_request(5, rerank=False))
            assert resp.success
            assert resp.extra["config"]["rerank"] is False
            assert resp.extra["config"]["local_rerank_enabled"] is False
            assert resp.extra["local_rerank"]["applied"] is False
            return [m["memory_id"] for m in resp.memories]
        finally:
            await store.close()

    ids = asyncio.run(_run())
    assert rerank_spies["factory"] == []
    assert rerank_spies["reranker"].calls == 0
    assert rerank_spies["graph"] == []
    assert len(ids) == 5


def test_rerank_default_true_keeps_rerank_stages(config, rerank_spies):
    async def _run():
        reader, store = await _reader_with(config, 8)
        try:
            req = _request(5)
            assert req.rerank is True
            resp = await reader.read(req)
            assert resp.success
            assert resp.extra["config"]["rerank"] is True
        finally:
            await store.close()

    asyncio.run(_run())
    assert len(rerank_spies["factory"]) == 1
    assert rerank_spies["reranker"].calls >= 1
    assert len(rerank_spies["graph"]) == 1


def test_rerank_is_per_request_under_concurrency(config, rerank_spies):
    """并发的两次 read 各按自己的 rerank 走，不互相影响。"""
    async def _run():
        reader, store = await _reader_with(config, 8)
        try:
            off, on = await asyncio.gather(
                reader.read(_request(5, rerank=False)),
                reader.read(_request(5, rerank=True)),
            )
            assert off.extra["config"]["rerank"] is False
            assert on.extra["config"]["rerank"] is True
            assert off.extra["local_rerank"]["applied"] is False
        finally:
            await store.close()

    asyncio.run(_run())
    assert len(rerank_spies["factory"]) == 1
    assert len(rerank_spies["graph"]) == 1


@pytest.mark.parametrize("kwargs, expected", [({}, True), ({"rerank": False}, False)])
def test_client_search_threads_rerank_into_read_request(kwargs, expected):
    """client.async_search → ReadRequest.rerank；默认 True（原行为）。"""
    from hy_memory.client import HyMemoryClient
    from hy_memory.pipelines.base import ReadResponse

    seen = {}

    class _Reader:
        VERSION = "fake"

        async def read(self, request):
            seen["rerank"] = request.rerank
            seen["limit"] = request.limit
            return ReadResponse()

    class _Registry:
        async def get_reader(self, reader_name=""):
            return _Reader()

    client = object.__new__(HyMemoryClient)
    client._config = MemoryConfig.from_env()
    client._cache = None
    client._history_store = None
    client._registry = _Registry()
    client._mode = "pro"

    out = asyncio.run(HyMemoryClient.async_search.__wrapped__(
        client, "q", user_ids=["u"], limit=7, **kwargs,
    ))
    assert "memories" in out
    assert seen == {"rerank": expected, "limit": 7}
