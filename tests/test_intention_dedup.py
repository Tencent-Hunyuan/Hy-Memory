# -*- coding: utf-8 -*-
"""
L7_INTENTION 层去重（0805 事故修复 · v2）单测。

覆盖：
  1. Recall dedup（含超采样 + memory_at/valid_until/cosine 联合判重 + 异步删）
  2. Writer batch dedup（只做批内，不查 VDB）
  3. READ_REQUEST 日志携带 intention_limit（可观测性）

不依赖 chromadb / venus embedding / numpy，全内存假 VDB。
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from hy_memory.models.memory import MemoryLayer, MemoryStatus, MemoryNode
from hy_memory.pipelines._retrieval.intention import (
    _cosine,
    _memory_at_day,
    _valid_until_key,
    _partition_duplicates,
    recall_intentions,
)


# ============================================================================
# Fakes
# ============================================================================


def _run(coro):
    """跑一次 coro，等所有后台任务（async delete）自然完成后再关 loop。"""
    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(coro)
        # 让 fire-and-forget 的 create_task 有机会执行完
        pending = [t for t in asyncio.all_tasks(loop) if not t.done()]
        if pending:
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        return result
    finally:
        loop.close()


class _FakeEmbedByHash:
    """按 text hash 分配 one-hot 稀疏向量：hash 相同 → cosine=1；不同 → cosine=0。"""

    def __init__(self, dims: int = 64):
        self.dims = dims

    def vec_for(self, text: str) -> List[float]:
        v = [0.0] * self.dims
        # 确定性哈希：内建 hash() 每个进程随机化，会让不同文案偶发撞格、
        # 被误判成复本，测试随 PYTHONHASHSEED 变 flaky。
        v[int(hashlib.md5(text.encode()).hexdigest(), 16) % self.dims] = 1.0
        return v

    async def embed_batch(self, texts):
        return [self.vec_for(t) for t in texts]

    async def embed_queued(self, text):
        return self.vec_for(text)


class _FakeVectorStore:
    """最小可用内存 VDB。search 按 dot-product 排序返回。"""

    def __init__(self):
        self._nodes: Dict[str, MemoryNode] = {}
        self.delete_calls: List[str] = []
        self.update_payload_calls: List[tuple] = []

    async def upsert(self, node: MemoryNode) -> str:
        nid = node.node_id or uuid.uuid4().hex
        node.node_id = nid
        self._nodes[nid] = node
        return nid

    async def delete(self, node_id: str):
        self.delete_calls.append(node_id)
        self._nodes.pop(node_id, None)

    async def update_payload(self, node_id: str, patch: Dict[str, Any]):
        self.update_payload_calls.append((node_id, patch))
        n = self._nodes.get(node_id)
        if not n:
            return
        for k, v in patch.items():
            setattr(n, k, v)

    async def search(
        self,
        *,
        query_embedding=None,
        user_ids=None,
        agent_ids=None,
        layers=None,
        limit: int = 10,
        score_threshold: float = 0.0,
        status_filter=None,
        only_latest: bool = True,
        **kwargs,
    ):
        results = []
        for nid, n in self._nodes.items():
            if layers and n.layer not in layers:
                continue
            if user_ids and n.user_id not in user_ids:
                continue
            if agent_ids and n.agent_id not in agent_ids:
                continue
            if status_filter and n.status not in status_filter:
                continue
            if only_latest and not n.is_latest:
                continue
            emb = n.embedding or []
            score = sum(a * b for a, b in zip(query_embedding or [], emb))
            if score < score_threshold:
                continue
            results.append({"node_id": nid, "node": n, "score": score})
        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:limit]


def _make_intention_node(
    content: str,
    *,
    user_id: str = "u1",
    agent_id: str = "a1",
    session_id: str = "s1",
    memory_at: Optional[datetime] = None,
    valid_until: Optional[datetime] = None,
    gmt_created: Optional[datetime] = None,
    tags: Optional[List[str]] = None,
    embedding: Optional[List[float]] = None,
    dims: int = 64,
) -> MemoryNode:
    n = MemoryNode(
        node_id=uuid.uuid4().hex,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        layer=MemoryLayer.L7_INTENTION,
        content=content,
        embedding=embedding or _FakeEmbedByHash(dims).vec_for(content),
        status=MemoryStatus.ACTIVE,
        is_latest=True,
        tags=list(tags or []),
        memory_at=memory_at,
        valid_until=valid_until,
    )
    if gmt_created is not None:
        n.gmt_created = gmt_created
    return n


# ============================================================================
# 1. Recall dedup 相关（含新的 memory_at + valid_until 判重规则）
# ============================================================================


def test_cosine_identical_and_orthogonal():
    """cosine 基础健壮性：同向量=1，正交=0，空/零向量=-inf。"""
    v = [1.0, 0.0, 0.0]
    assert _cosine(v, v) == 1.0
    assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert _cosine([], v) == float("-inf")
    assert _cosine([0.0, 0.0], v) == float("-inf")


def test_memory_at_day_and_valid_until_key():
    """memory_at / valid_until 归一化到 date 键。"""
    dt = datetime(2025, 8, 6, 15, 30)
    node = _make_intention_node("x", memory_at=dt, valid_until=dt)
    assert _memory_at_day(node) == date(2025, 8, 6)
    assert _valid_until_key(node) == date(2025, 8, 6)

    node2 = _make_intention_node("x")
    assert _memory_at_day(node2) is None
    assert _valid_until_key(node2) is None


def test_partition_duplicates_same_day_same_valid_until_high_cosine_dedup():
    """同天 memory_at + 同 valid_until + cosine≈1 → 判重。保留 gmt 最早。"""
    same_ma = datetime(2025, 8, 6, 10, 0)
    same_vu = datetime(2025, 8, 8, 23, 59, 59)
    n1 = _make_intention_node(
        "用户月底要发布新品", memory_at=same_ma, valid_until=same_vu,
        gmt_created=datetime(2025, 8, 6, 10, 0),
    )
    n2 = _make_intention_node(
        "用户月底要发布新品", memory_at=same_ma, valid_until=same_vu,
        gmt_created=datetime(2025, 8, 6, 10, 5),  # 更晚
    )
    n3 = _make_intention_node(
        "用户月底要发布新品", memory_at=same_ma, valid_until=same_vu,
        gmt_created=datetime(2025, 8, 6, 10, 10),  # 最晚
    )
    hits = [
        {"node_id": n.node_id, "node": n, "score": 0.68}
        for n in (n2, n1, n3)  # 故意打乱顺序，测试保 gmt 最早不是保第一条
    ]
    kept, delete_ids = _partition_duplicates(hits, threshold=0.95)
    assert len(kept) == 1
    assert kept[0]["node_id"] == n1.node_id  # gmt 最早
    assert set(delete_ids) == {n2.node_id, n3.node_id}


def test_partition_duplicates_different_memory_at_day_keeps_both():
    """content + valid_until 都一样，但 memory_at 不同天 → 不判重。"""
    n_this_week = _make_intention_node(
        "用户周五要开会",
        memory_at=datetime(2025, 8, 4, 9, 0),   # 周一
        valid_until=datetime(2025, 8, 8, 23, 59),
    )
    n_next_week = _make_intention_node(
        "用户周五要开会",
        memory_at=datetime(2025, 8, 11, 9, 0),  # 下周一
        valid_until=datetime(2025, 8, 15, 23, 59),
    )
    hits = [
        {"node_id": n_this_week.node_id, "node": n_this_week, "score": 0.7},
        {"node_id": n_next_week.node_id, "node": n_next_week, "score": 0.7},
    ]
    kept, delete_ids = _partition_duplicates(hits, threshold=0.95)
    assert len(kept) == 2
    assert delete_ids == []


def test_partition_duplicates_different_valid_until_keeps_both():
    """核心 case：同天说的两个"周五开会"，指向不同会议（valid_until 不同）→ 保留两条。

    "用户今天上午说 8/8 开会" vs "今天下午说 8/15 开会"，content 一样、memory_at
    同天，但 valid_until 不同。不能合并。
    """
    same_ma = datetime(2025, 8, 6, 10, 0)
    n_this_fri = _make_intention_node(
        "用户周五要开会", memory_at=same_ma,
        valid_until=datetime(2025, 8, 8, 23, 59),   # 本周五
    )
    n_next_fri = _make_intention_node(
        "用户周五要开会", memory_at=same_ma,
        valid_until=datetime(2025, 8, 15, 23, 59),  # 下周五
    )
    hits = [
        {"node_id": n_this_fri.node_id, "node": n_this_fri, "score": 0.7},
        {"node_id": n_next_fri.node_id, "node": n_next_fri, "score": 0.7},
    ]
    kept, delete_ids = _partition_duplicates(hits, threshold=0.95)
    assert len(kept) == 2, "different valid_until must not be deduped"
    assert delete_ids == []


def test_partition_duplicates_low_cosine_keeps_both():
    """content 不同（cosine 低）→ 不判重，即使 memory_at / valid_until 同。"""
    same_ma = datetime(2025, 8, 6)
    same_vu = datetime(2025, 8, 8)
    n1 = _make_intention_node(
        "用户周五要开会", memory_at=same_ma, valid_until=same_vu,
        embedding=[1.0, 0.0],
    )
    n2 = _make_intention_node(
        "用户周五要看牙医", memory_at=same_ma, valid_until=same_vu,
        embedding=[0.0, 1.0],
    )
    hits = [
        {"node_id": n1.node_id, "node": n1, "score": 0.6},
        {"node_id": n2.node_id, "node": n2, "score": 0.6},
    ]
    kept, delete_ids = _partition_duplicates(hits, threshold=0.95)
    assert len(kept) == 2
    assert delete_ids == []


def test_partition_duplicates_mixed_scenarios():
    """混合场景：4 条月底新品（同 memory_at + 同 valid_until）
                + 2 条家长会（同 memory_at + 不同 valid_until）
                + 1 条千岛湖（其他）→ 期望 kept=4 (1 新品 + 2 家长会 + 1 千岛湖)。"""
    ma = datetime(2025, 8, 6)
    vu_end_month = datetime(2025, 8, 31)
    vu_this_fri = datetime(2025, 8, 8)
    vu_next_fri = datetime(2025, 8, 15)

    p1 = _make_intention_node(
        "月底发新品", memory_at=ma, valid_until=vu_end_month,
        gmt_created=datetime(2025, 8, 1),
    )
    p2 = _make_intention_node(
        "月底发新品", memory_at=ma, valid_until=vu_end_month,
        gmt_created=datetime(2025, 8, 2),
    )
    p3 = _make_intention_node(
        "月底发新品", memory_at=ma, valid_until=vu_end_month,
        gmt_created=datetime(2025, 8, 3),
    )
    p4 = _make_intention_node(
        "月底发新品", memory_at=ma, valid_until=vu_end_month,
        gmt_created=datetime(2025, 8, 4),
    )
    m1 = _make_intention_node("家长会", memory_at=ma, valid_until=vu_this_fri)
    m2 = _make_intention_node("家长会", memory_at=ma, valid_until=vu_next_fri)
    q1 = _make_intention_node("千岛湖", memory_at=ma, valid_until=None)

    hits = [
        {"node_id": n.node_id, "node": n, "score": 0.6}
        for n in (p1, p2, p3, p4, m1, m2, q1)
    ]
    kept, delete_ids = _partition_duplicates(hits, threshold=0.95)

    kept_ids = {h["node_id"] for h in kept}
    # 4 条新品压成 1（gmt 最早的 p1）；2 条家长会不同 valid_until 全留；千岛湖留
    assert p1.node_id in kept_ids
    assert m1.node_id in kept_ids
    assert m2.node_id in kept_ids
    assert q1.node_id in kept_ids
    assert set(delete_ids) == {p2.node_id, p3.node_id, p4.node_id}
    assert len(kept) == 4


# ============================================================================
# recall_intentions 端到端
# ============================================================================


def test_recall_intentions_dedup_and_async_delete_end_to_end():
    """端到端：VDB 里 3 条完全一样的月底新品 + 1 条家长会；limit=3 时 kept=2，
    另 2 条月底新品异步从 VDB 被删。"""
    vs = _FakeVectorStore()
    emb = _FakeEmbedByHash(dims=32)
    fixed_now = datetime(2025, 8, 6, 12, 0)
    ma = datetime(2025, 8, 6)
    vu = datetime(2025, 8, 31, 23, 59)

    p_ids = []
    for i in range(3):
        n = _make_intention_node(
            "用户月底要发布新品", dims=32,
            memory_at=ma, valid_until=vu,
            gmt_created=datetime(2025, 8, 1 + i),
            embedding=emb.vec_for("用户月底要发布新品"),
        )
        _run(vs.upsert(n))
        p_ids.append(n.node_id)

    m = _make_intention_node(
        "用户下周五要开家长会", dims=32,
        memory_at=ma, valid_until=datetime(2025, 8, 15),
        embedding=emb.vec_for("用户下周五要开家长会"),
    )
    _run(vs.upsert(m))

    # query 用"月底新品"，dot=1.0 排前 3；家长会 dot=0
    q = emb.vec_for("用户月底要发布新品")
    result = _run(recall_intentions(
        vs, q, user_ids=["u1"], agent_ids=["a1"], limit=3, now=fixed_now,
    ))

    # 4 条 hit，dedup 后 kept=2（1 新品 + 1 家长会），限 3 返回 2
    contents = [h["node"].content for h in result]
    assert contents.count("用户月底要发布新品") == 1
    # p1（gmt 最早）应该留下，其余 2 条被异步删
    assert p_ids[0] in {h["node_id"] for h in result}
    assert set(vs.delete_calls) == {p_ids[1], p_ids[2]}


def test_recall_intentions_different_valid_until_all_returned():
    """两个 memory_at 相同、valid_until 不同的意图 → 都召回，都不删。"""
    vs = _FakeVectorStore()
    emb = _FakeEmbedByHash(dims=32)
    fixed_now = datetime(2025, 8, 6, 12, 0)
    ma = datetime(2025, 8, 6)

    n_this = _make_intention_node(
        "用户周五要开会", dims=32, memory_at=ma,
        valid_until=datetime(2025, 8, 8, 23, 59),
        embedding=emb.vec_for("用户周五要开会"),
    )
    n_next = _make_intention_node(
        "用户周五要开会", dims=32, memory_at=ma,
        valid_until=datetime(2025, 8, 15, 23, 59),
        embedding=emb.vec_for("用户周五要开会"),
    )
    _run(vs.upsert(n_this))
    _run(vs.upsert(n_next))

    q = emb.vec_for("用户周五要开会")
    result = _run(recall_intentions(vs, q, user_ids=["u1"], limit=10, now=fixed_now))
    assert len(result) == 2
    assert vs.delete_calls == []


def test_recall_intentions_expired_converted_to_l2():
    """valid_until 已过 → 惰性 update_payload 转 L2_FACT，不进返回结果。"""
    vs = _FakeVectorStore()
    emb = _FakeEmbedByHash(dims=32)
    yesterday = datetime.now() - timedelta(days=1)

    n = _make_intention_node(
        "老的过期意图", dims=32,
        memory_at=datetime(2025, 1, 1),
        valid_until=yesterday,
        embedding=emb.vec_for("老的过期意图"),
    )
    _run(vs.upsert(n))
    q = emb.vec_for("老的过期意图")
    result = _run(recall_intentions(vs, q, user_ids=["u1"], limit=10))
    assert result == []
    assert vs.update_payload_calls == [(n.node_id, {"layer": MemoryLayer.L2_FACT.value})]


def test_recall_intentions_limit_zero_short_circuit():
    """limit=0 → 立即返回 []，不查 VDB。"""
    vs = _FakeVectorStore()
    result = _run(recall_intentions(vs, [1.0] * 32, user_ids=["u1"], limit=0))
    assert result == []


def test_recall_intentions_oversample_larger_than_limit():
    """fetch_limit 应比 limit 大很多；VDB 里 10 条完全正交的意图 + limit=3
    → 应能拿到 3 条不重复；由于 embedding 完全正交，dedup 一条都不该删。"""
    vs = _FakeVectorStore()
    fixed_now = datetime(2025, 8, 6, 12, 0)
    ma = datetime(2025, 8, 6)
    dims = 128

    def _one_hot(idx: int) -> List[float]:
        v = [0.0] * dims
        v[idx] = 1.0
        return v

    # 用明确的 one-hot 索引，保证 10 条向量完全正交（cosine=0）
    for i in range(10):
        n = _make_intention_node(
            f"意图 #{i}", dims=dims, memory_at=ma,
            embedding=_one_hot(i),
        )
        _run(vs.upsert(n))

    # query 命中意图 #3（cosine=1），其他 cosine=0
    q = _one_hot(3)
    result = _run(recall_intentions(
        vs, q, user_ids=["u1"], limit=3, now=fixed_now,
    ))
    # limit=3 返回不超过 3 条；且没有任何 delete（向量全正交）
    assert len(result) <= 3
    assert vs.delete_calls == []


# ============================================================================
# 2. Writer batch dedup（只做批内、不查 VDB）
# ============================================================================


def _make_writer():
    from hy_memory.config import MemoryConfig
    from hy_memory.pipelines.writer import MemoryWriter
    cfg = MemoryConfig.from_env()
    writer = MemoryWriter(
        cfg,
        embed_service=_FakeEmbedByHash(dims=32),
        vector_store=_FakeVectorStore(),
        cache=None,
    )
    return writer


def _make_req(user_id="u1", agent_id="a1"):
    from hy_memory.pipelines.base import WriteRequest
    return WriteRequest(
        content="", messages=[], user_id=user_id, agent_id=agent_id,
        session_id="s1", request_id=uuid.uuid4().hex,
        memory_at=datetime(2025, 8, 6, 10, 0),
    )


def test_writer_batch_dedup_pure_function_same_valid_until():
    """纯函数：3 条同 content + 同 valid_until → drop 2 条。"""
    from hy_memory.pipelines.writer import MemoryWriter
    emb = _FakeEmbedByHash(dims=16)
    vu = datetime(2025, 8, 8)
    intentions = [
        {"content": "月底发新品", "valid_until": vu, "tags": ["work"]},
        {"content": "月底发新品", "valid_until": vu, "tags": ["deadline"]},
        {"content": "月底发新品", "valid_until": vu, "tags": ["product"]},
    ]
    embeddings = [emb.vec_for(it["content"]) for it in intentions]
    drop_idx = MemoryWriter._intention_batch_dedup(intentions, embeddings)
    assert drop_idx == [1, 2]


def test_writer_batch_dedup_different_valid_until_keeps_all():
    """核心 case：同 content，valid_until 不同 → 都保留，不判重。"""
    from hy_memory.pipelines.writer import MemoryWriter
    emb = _FakeEmbedByHash(dims=16)
    intentions = [
        {"content": "周五要开会", "valid_until": datetime(2025, 8, 8)},   # 本周五
        {"content": "周五要开会", "valid_until": datetime(2025, 8, 15)},  # 下周五
    ]
    embeddings = [emb.vec_for(it["content"]) for it in intentions]
    drop_idx = MemoryWriter._intention_batch_dedup(intentions, embeddings)
    assert drop_idx == []


def test_writer_batch_dedup_none_valid_until_all_none_treated_same():
    """valid_until 全为 None 且同 content → 依然判重（None 视作同一 key）。"""
    from hy_memory.pipelines.writer import MemoryWriter
    emb = _FakeEmbedByHash(dims=16)
    intentions = [
        {"content": "老板画的饼", "valid_until": None},
        {"content": "老板画的饼", "valid_until": None},
    ]
    embeddings = [emb.vec_for(it["content"]) for it in intentions]
    drop_idx = MemoryWriter._intention_batch_dedup(intentions, embeddings)
    assert drop_idx == [1]


def test_writer_batch_dedup_none_vs_some_not_deduped():
    """一条 valid_until=None，一条有 → 分属不同桶，不判重。"""
    from hy_memory.pipelines.writer import MemoryWriter
    emb = _FakeEmbedByHash(dims=16)
    intentions = [
        {"content": "写小说", "valid_until": None},
        {"content": "写小说", "valid_until": datetime(2025, 12, 31)},
    ]
    embeddings = [emb.vec_for(it["content"]) for it in intentions]
    drop_idx = MemoryWriter._intention_batch_dedup(intentions, embeddings)
    assert drop_idx == []


def test_writer_batch_dedup_different_content_keeps_all():
    """content 不同 → cosine 低，不判重。"""
    from hy_memory.pipelines.writer import MemoryWriter
    # 用明确的 one-hot 索引保证 3 个 embedding 完全正交（cosine=0）
    def _one_hot(idx: int, dims: int = 64) -> List[float]:
        v = [0.0] * dims
        v[idx] = 1.0
        return v
    intentions = [
        {"content": "月底发新品", "valid_until": None},
        {"content": "周五开家长会", "valid_until": None},
        {"content": "国庆去千岛湖", "valid_until": None},
    ]
    embeddings = [_one_hot(0), _one_hot(1), _one_hot(2)]
    drop_idx = MemoryWriter._intention_batch_dedup(intentions, embeddings)
    assert drop_idx == []


def test_writer_store_intentions_e2e_batch_dedup():
    """_store_intentions 端到端：3 条复读 + 1 条新意图 → VDB 里只落 2 个。"""
    vs = _FakeVectorStore()
    from hy_memory.config import MemoryConfig
    from hy_memory.pipelines.writer import MemoryWriter
    cfg = MemoryConfig.from_env()
    writer = MemoryWriter(cfg, embed_service=_FakeEmbedByHash(dims=32),
                          vector_store=vs, cache=None)
    req = _make_req()
    vu = datetime(2025, 8, 8)
    intentions = [
        {"content": "月底发新品", "valid_until": vu, "tags": ["a"]},
        {"content": "月底发新品", "valid_until": vu, "tags": ["b"]},
        {"content": "月底发新品", "valid_until": vu, "tags": ["c"]},
        {"content": "周五要开会", "valid_until": vu, "tags": []},
    ]
    ids = _run(writer._store_intentions(intentions, req, vs, req.request_id))
    assert len(ids) == 2  # 3 条月底新品压成 1 + 周五开会 1
    assert len(vs._nodes) == 2


# ============================================================================
# 3. READ_REQUEST 日志携带 intention_limit
# ============================================================================


def test_log_request_records_intention_limit():
    import json
    from hy_memory.pipelines._retrieval.trace import ReadTraceLogger

    captured = []

    class _StubCache:
        async def store_pipeline_log(self, **kwargs):
            captured.append(kwargs)

    async def _main():
        tl = ReadTraceLogger(cache=_StubCache(), request_id="r",
                             user_id="u", agent_id="a", reader_version="v")
        await tl.log_request(
            query="q", limit=5, layers=None, min_score=0.0,
            schema_min_score=None, schema_limit=None,
            user_ids=[], agent_ids=[], session_ids=[],
            intention_limit=20,
        )

    _run(_main())
    parsed = captured[-1]["parsed"]
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    assert parsed["intention_limit"] == 20


def test_log_request_intention_limit_default_none():
    import json
    from hy_memory.pipelines._retrieval.trace import ReadTraceLogger

    captured = []

    class _StubCache:
        async def store_pipeline_log(self, **kwargs):
            captured.append(kwargs)

    async def _main():
        tl = ReadTraceLogger(cache=_StubCache(), request_id="r",
                             user_id="u", agent_id="a", reader_version="v")
        await tl.log_request(
            query="q", limit=5, layers=None, min_score=0.0,
            schema_min_score=None, schema_limit=None,
            user_ids=[], agent_ids=[], session_ids=[],
        )

    _run(_main())
    parsed = captured[-1]["parsed"]
    if isinstance(parsed, str):
        parsed = json.loads(parsed)
    assert "intention_limit" in parsed
    assert parsed["intention_limit"] is None
