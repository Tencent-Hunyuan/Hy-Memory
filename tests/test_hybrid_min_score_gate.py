# -*- coding: utf-8 -*-
"""
hybrid 出口阈值 + 按条加成打分回归。

新公式：final = sem + 𝟙[bm25>0]·w_bm25·bm25 + 𝟙[tag]·w_tag·tag_cos
无 BM25 的记忆不再被全局 has_bm25 乘 0.6。
"""

import asyncio
import math
import uuid

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.models.memory import MemoryNode, MemoryLayer, MemoryStatus
from hy_memory.pipelines.base import ReadRequest


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _unit(vec):
    n = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / n for x in vec]


def _q():
    return _unit([1.0] + [0.0] * 31)


def _vec_a():
    return _unit([0.8, 0.6] + [0.0] * 30)


def _vec_b():
    s = 0.25
    return _unit([s, math.sqrt(1 - s * s)] + [0.0] * 30)


@pytest.fixture
def config(tmp_path):
    return MemoryConfig.from_dict({"vector_store": {
        "provider": "chroma",
        "collection_name": f"t_minscore_{uuid.uuid4().hex[:8]}",
        "persist_directory": str(tmp_path / "chroma"),
        "embedding_dims": 32,
    }})


def _mk(node_id, content, vec, tags=None):
    n = MemoryNode(
        node_id=node_id, user_id="u1", agent_id="a1", session_id="s1",
        content=content, layer=MemoryLayer.L2_FACT,
        status=MemoryStatus.ACTIVE, is_latest=True,
        tags=list(tags or []),
    )
    n.embedding = vec
    return n


class TestHybridAdditiveScore:
    def test_real_fts_keyword_only_score_keeps_public_threshold(self, config):
        """A real FTS symbol hit passes the usual gate, but not a stricter one."""
        from hy_memory.data.vector_store_chroma import ChromaVectorStore
        from hy_memory.pipelines.reader_hybrid import HybridReadPipeline

        config.vector_store.chroma_lexical_enabled = True
        store = ChromaVectorStore(config)
        run(store.initialize())
        try:
            # Zero cosine excludes this record from semantic admission.
            run(store.upsert(_mk(
                "keyword-only", "ECONNRESET requires refreshing the socket pool", _unit([0.0, 1.0] + [0.0] * 30),
            )))
            lexical = run(store.keyword_search("ECONNRESET", user_id="u1"))
            assert [hit["node_id"] for hit in lexical] == ["keyword-only"]
            assert 0.9 <= lexical[0]["score"] <= 1.0

            reader = HybridReadPipeline(config, vector_store=store)
            run(reader.initialize())
            ordinary = run(reader.read(ReadRequest(
                query="ECONNRESET", query_embedding=_q(), user_ids=["u1"],
                agent_ids=["a1"], min_score=0.4,
            )))
            assert [memory["memory_id"] for memory in ordinary.memories] == ["keyword-only"]
            assert ordinary.memories[0]["score"] >= 0.4

            strict = run(reader.read(ReadRequest(
                query="ECONNRESET", query_embedding=_q(), user_ids=["u1"],
                agent_ids=["a1"], min_score=0.99,
            )))
            assert strict.memories == []
        finally:
            run(store.close())

    def test_no_bm25_keeps_full_sem_above_min_score(self, config, monkeypatch):
        """B 无 BM25：旧逻辑 final=sem*0.6 跌破 0.2；新逻辑 final=sem 应保留。"""
        from hy_memory.data.vector_store_chroma import ChromaVectorStore
        from hy_memory.pipelines.reader_hybrid import HybridReadPipeline

        monkeypatch.setenv("MEMORY_HYBRID_V2_VDB_WEIGHT_BM25", "0.2")
        monkeypatch.setenv("MEMORY_HYBRID_V2_VDB_WEIGHT_TAG", "0.3")

        store = ChromaVectorStore(config)
        run(store.initialize())
        try:
            run(store.upsert(_mk("A", "用户最喜欢南山水库", _vec_a())))
            run(store.upsert(_mk("B", "用户计划从瑜伽开始培养运动习惯", _vec_b())))

            reader = HybridReadPipeline(config, vector_store=store)
            run(reader.initialize())

            async def _fake_kw(query, top_k=10, user_id=None, agent_ids=None,
                               layers=None, status_filter=None, only_latest=True, **kw):
                return [{"node_id": "A", "score": 0.5, "node": None}]

            store.keyword_search = _fake_kw

            req = ReadRequest(
                query="我最喜欢的郊游目的地是哪个",
                query_embedding=_q(),
                user_ids=["u1"], agent_ids=["a1"],
                limit=10, min_score=0.2, schema_min_score=0.2,
            )
            resp = run(reader.read(req))

            ids = {m["memory_id"]: m["score"] for m in resp.memories}
            assert "A" in ids, f"A 应保留: {ids}"
            assert "B" in ids, (
                f"B 无 BM25 但 sem≈0.25 应保留（不再×0.6），实际 {ids}"
            )
            assert ids["B"] >= 0.2
            for m in resp.memories:
                if m.get("source") in ("vdb", "vdb_keyword_only"):
                    assert m["score"] >= 0.2
        finally:
            run(store.close())

    def test_below_min_score_still_filtered(self, config, monkeypatch):
        """sem 本身低于 min_score 的仍应被滤掉。"""
        from hy_memory.data.vector_store_chroma import ChromaVectorStore
        from hy_memory.pipelines.reader_hybrid import HybridReadPipeline

        store = ChromaVectorStore(config)
        run(store.initialize())
        try:
            # cos≈0.1 < min_score 0.2
            low = _unit([0.1, math.sqrt(1 - 0.1 * 0.1)] + [0.0] * 30)
            run(store.upsert(_mk("L", "完全无关的内容xyz", low)))

            reader = HybridReadPipeline(config, vector_store=store)
            run(reader.initialize())

            async def _fake_kw(**kw):
                return []

            store.keyword_search = _fake_kw

            req = ReadRequest(
                query="我最喜欢的郊游目的地是哪个",
                query_embedding=_q(),
                user_ids=["u1"], agent_ids=["a1"],
                limit=10, min_score=0.2, schema_min_score=0.2,
            )
            resp = run(reader.read(req))
            ids = {m["memory_id"] for m in resp.memories}
            assert "L" not in ids
        finally:
            run(store.close())
