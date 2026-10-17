# -*- coding: utf-8 -*-
"""
真实端到端测试：Chroma VDB + recall_intentions dedup

不 mock VDB，用 chromadb PersistentClient (临时目录) 完整验证:
  1. upsert 3 条相同 content + memory_at + valid_until 的意图节点到 Chroma
  2. 调 recall_intentions 判重
  3. 验证:
     - 返回结果只有 1 条（复本被合并）
     - VDB 里被异步删掉 2 条（后台 create_task 完成后 delete_calls）

这个测试的**唯一目的**是暴露 mock VDB 掩盖的问题:
  Chroma search 默认 include=["metadatas", "documents", "distances"] 不含
  embeddings → hit 的 node.embedding 是 None → _partition_duplicates 里
  cosine 判重会因为拿不到 embedding 而静默降级为"不判重"。
"""

import asyncio
import hashlib
import shutil
import tempfile
import uuid
from datetime import datetime

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.data.vector_store_chroma import ChromaVectorStore
from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus
from hy_memory.pipelines._retrieval.intention import recall_intentions


def _one_hot(idx: int, dims: int = 32):
    v = [0.0] * dims
    v[idx] = 1.0
    return v


def _make_node(content, embedding, memory_at, valid_until,
               user_id="u_e2e", agent_id="a_e2e"):
    return MemoryNode(
        node_id=uuid.uuid4().hex,
        user_id=user_id,
        agent_id=agent_id,
        session_id="s_e2e",
        layer=MemoryLayer.L7_INTENTION,
        content=content,
        embedding=embedding,
        status=MemoryStatus.ACTIVE,
        is_latest=True,
        memory_at=memory_at,
        valid_until=valid_until,
    )


@pytest.mark.asyncio
async def test_recall_intentions_e2e_with_real_chroma():
    """真实 Chroma 后端：3 条相同意图 → 只返回 1 条 + 异步删 2 条。"""
    tmp = tempfile.mkdtemp(prefix="hymem_e2e_")
    try:
        cfg = MemoryConfig.from_env()
        cfg.vector_store.backend = "chroma"
        cfg.vector_store.persist_directory = tmp
        # 用独立 collection 名避免残留
        vs = ChromaVectorStore(cfg)
        # 给 collection 一个 unique 名字
        vs._collection_name = f"e2e_test_{uuid.uuid4().hex[:8]}"
        await vs.initialize()

        ma = datetime(2026, 8, 6, 10, 0)  # 系统时间是 2026-08-06，用未来 valid_until 避免过期
        vu = datetime(2026, 8, 31, 23, 59)
        emb = _one_hot(3)

        # 3 条完全一样的月底新品（同 memory_at + 同 valid_until + 同 embedding）
        p_ids = []
        for i in range(3):
            n = _make_node("用户月底要发布新品", emb, ma, vu)
            await vs.upsert(n)
            p_ids.append(n.node_id)

        # 1 条不同的家长会
        m = _make_node("用户下周五要开家长会", _one_hot(7), ma, datetime(2026, 8, 15))
        await vs.upsert(m)

        # query 用月底新品向量，score 排序前 3 应是新品
        result = await recall_intentions(
            vs, emb,
            user_ids=["u_e2e"], agent_ids=["a_e2e"],
            limit=3,
            now=datetime(2026, 8, 6, 12, 0),
        )

        contents = [h["node"].content for h in result]
        print(f"\n[E2E] returned contents: {contents}")
        print(f"[E2E] returned count: {len(result)}")

        # 期望：3 条复本合成 1 条 + 1 条家长会 = 2 条独立意图
        # 断言：结果里"月底新品"应该只出现 1 次
        assert contents.count("用户月底要发布新品") == 1, \
            f"E2E FAIL: expected 1 copy of '月底新品' after dedup, got {contents.count('用户月底要发布新品')}"

        # 等异步删除任务完成
        pending = [t for t in asyncio.all_tasks() if not t.done() and t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        # 验证 VDB 里真的少了 2 条
        remaining_new_products = 0
        for pid in p_ids:
            got = await vs.get_by_id(pid)
            if got is not None:
                remaining_new_products += 1
        print(f"[E2E] VDB remaining new-product nodes: {remaining_new_products} (expected 1)")
        assert remaining_new_products == 1, \
            f"E2E FAIL: expected 1 new-product node left after async delete, got {remaining_new_products}"

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_different_valid_until_e2e_not_deduped():
    """真实 Chroma：同 content + 同 memory_at + 不同 valid_until → 两条都保留。

    对应你和同事讨论的核心 case：
      "上午说这周五开会" vs "下午说下周五开会"
      → 内容和 memory_at 一样，但 valid_until 不同，属于两个不同事件，不能合并。
    """
    tmp = tempfile.mkdtemp(prefix="hymem_e2e_")
    try:
        cfg = MemoryConfig.from_env()
        cfg.vector_store.backend = "chroma"
        cfg.vector_store.persist_directory = tmp
        vs = ChromaVectorStore(cfg)
        vs._collection_name = f"e2e_test_{uuid.uuid4().hex[:8]}"
        await vs.initialize()

        ma = datetime(2026, 8, 6, 10, 0)
        emb = _one_hot(5)

        # 两条 content 相同 embedding 相同的意图，但 valid_until 不同（两次不同会议）
        n1 = _make_node("周五要开会", emb, ma, datetime(2026, 8, 8))    # 本周五
        n2 = _make_node("周五要开会", emb, ma, datetime(2026, 8, 15))   # 下周五
        await vs.upsert(n1)
        await vs.upsert(n2)

        result = await recall_intentions(
            vs, emb,
            user_ids=["u_e2e"], agent_ids=["a_e2e"],
            limit=10,
            now=datetime(2026, 8, 6, 12, 0),
        )

        assert len(result) == 2, \
            f"E2E FAIL: different valid_until should NOT be deduped, got {len(result)} results"

        # 等异步（应该没有 delete 任务，但保险起见）
        pending = [t for t in asyncio.all_tasks() if not t.done() and t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        # VDB 里两条都应该还在
        assert await vs.get_by_id(n1.node_id) is not None
        assert await vs.get_by_id(n2.node_id) is not None
        print("\n[E2E-different-vu] PASSED")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.asyncio
async def test_writer_batch_dedup_e2e():
    """真实 Chroma：writer._store_intentions 端到端，3 条同意图批内被 dedup 成 1 条落库。"""
    from hy_memory.pipelines.base import WriteRequest
    from hy_memory.pipelines.writer import MemoryWriter

    class _FakeEmbedByHash:
        """给不同 text 返回不同 one-hot 向量。"""
        def __init__(self, dims=32):
            self.dims = dims

        def _vec(self, text):
            # 确定性哈希：内建 hash() 每个进程随机化，两条不同文案约有 1/dims
            # 概率撞进同一格，被误判成复本，整个测试随 PYTHONHASHSEED 变 flaky。
            v = [0.0] * self.dims
            v[int(hashlib.md5(text.encode()).hexdigest(), 16) % self.dims] = 1.0
            return v

        async def embed_batch(self, texts):
            return [self._vec(t) for t in texts]

        async def embed_queued(self, text):
            return self._vec(text)

    tmp = tempfile.mkdtemp(prefix="hymem_e2e_")
    try:
        cfg = MemoryConfig.from_env()
        cfg.vector_store.backend = "chroma"
        cfg.vector_store.persist_directory = tmp
        vs = ChromaVectorStore(cfg)
        vs._collection_name = f"e2e_test_{uuid.uuid4().hex[:8]}"
        await vs.initialize()

        writer = MemoryWriter(
            cfg, embed_service=_FakeEmbedByHash(dims=32),
            vector_store=vs, cache=None,
        )
        req = WriteRequest(
            content="", messages=[], user_id="u_e2e", agent_id="a_e2e",
            session_id="s_e2e", request_id=uuid.uuid4().hex,
            memory_at=datetime(2026, 8, 6, 10, 0),
        )
        vu = datetime(2026, 8, 8)
        intentions = [
            {"content": "月底发新品", "valid_until": vu, "tags": ["a"]},
            {"content": "月底发新品", "valid_until": vu, "tags": ["b"]},
            {"content": "月底发新品", "valid_until": vu, "tags": ["c"]},
            {"content": "周五开家长会", "valid_until": vu, "tags": []},
        ]
        ids = await writer._store_intentions(intentions, req, vs, req.request_id)
        print(f"\n[E2E-writer] stored ids: {len(ids)} (expected 2)")

        assert len(ids) == 2, \
            f"E2E FAIL: 3 duplicate intentions should collapse to 1, got {len(ids)} stored"

        # 从 VDB 侧再确认
        for nid in ids:
            got = await vs.get_by_id(nid)
            assert got is not None

        print("[E2E-writer] PASSED")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(test_recall_intentions_e2e_with_real_chroma())
    print("\n[E2E-recall] PASSED")
    asyncio.run(test_different_valid_until_e2e_not_deduped())
    asyncio.run(test_writer_batch_dedup_e2e())
    print("\n[E2E ALL PASSED]")
