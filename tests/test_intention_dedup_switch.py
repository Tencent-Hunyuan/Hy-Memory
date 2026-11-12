# -*- coding: utf-8 -*-
"""
Intention（L7）读侧判重开关（issue #241）。

覆盖：
  1. 默认开启：行为不变，复本只返回最早一条，其余异步删库；
  2. 关闭：不隐藏、不删除，两条意图都返回；
  3. 阈值生效：cosine 低于阈值不判重，高于阈值判重；
  4. reader 按 client 的 MemoryConfig.recall 读取开关/阈值，而非调用时的 env；
  5. MemoryConfig 默认值与 env 回退；
  6. 写入侧批内判重同样受开关控制；阈值不影响写入侧；
  7. 关闭时不为判重补拉 embedding；配置阈值非法时不影响召回。
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.pipelines.base import ReadRequest
from hy_memory.pipelines._retrieval.intention import recall_intentions

from tests.test_intention_dedup import (
    _FakeVectorStore,
    _make_intention_node,
    _run,
)


_MA = datetime(2025, 8, 6)
_VU = datetime(2025, 8, 31, 23, 59)
_NOW = datetime(2025, 8, 6, 12, 0)


def _unit(vec):
    n = sum(x * x for x in vec) ** 0.5 or 1.0
    return [x / n for x in vec]


def _seed_project_pair(vs, *, emb_a, emb_b):
    """A / B 两个项目的近似意图：同 user/agent/同天/同截止日。A 更早写入。"""
    a = _make_intention_node(
        "A 项目每周一触发发布前校验流程", memory_at=_MA, valid_until=_VU,
        gmt_created=datetime(2025, 8, 1), embedding=emb_a,
    )
    b = _make_intention_node(
        "B 项目每周一触发发布前校验流程", memory_at=_MA, valid_until=_VU,
        gmt_created=datetime(2025, 8, 2), embedding=emb_b,
    )
    _run(vs.upsert(a))
    _run(vs.upsert(b))
    return a, b


# ============================================================================
# recall_intentions 参数
# ============================================================================


def test_default_on_keeps_earliest_and_deletes_duplicate():
    vs = _FakeVectorStore()
    emb = _unit([1.0, 0.0])
    a, b = _seed_project_pair(vs, emb_a=emb, emb_b=list(emb))
    stats = {}
    result = _run(recall_intentions(
        vs, emb, user_ids=["u1"], limit=10, now=_NOW, stats_out=stats,
    ))
    assert [h["node_id"] for h in result] == [a.node_id]
    assert vs.delete_calls == [b.node_id]
    assert stats["dedup_enabled"] is True
    assert stats["dedup_dropped"] == 1


def test_off_returns_both_and_deletes_nothing():
    vs = _FakeVectorStore()
    emb = _unit([1.0, 0.0])
    a, b = _seed_project_pair(vs, emb_a=emb, emb_b=list(emb))
    stats = {}
    result = _run(recall_intentions(
        vs, emb, user_ids=["u1"], limit=10, now=_NOW, stats_out=stats,
        dedup_enabled=False,
    ))
    assert {h["node_id"] for h in result} == {a.node_id, b.node_id}
    assert vs.delete_calls == []
    assert stats["dedup_enabled"] is False
    assert stats["dedup_dropped"] == 0


def test_threshold_respected():
    # cosine(a, b) = 0.9
    emb_a = _unit([1.0, 0.0])
    emb_b = _unit([0.9, (1 - 0.81) ** 0.5])

    # 默认 0.95 → 0.9 不算复本
    vs = _FakeVectorStore()
    _seed_project_pair(vs, emb_a=emb_a, emb_b=emb_b)
    result = _run(recall_intentions(
        vs, emb_a, user_ids=["u1"], limit=10, now=_NOW, dedup_threshold=0.95,
    ))
    assert len(result) == 2
    assert vs.delete_calls == []

    # 调低到 0.85 → 判重，删较晚的 B
    vs = _FakeVectorStore()
    a, b = _seed_project_pair(vs, emb_a=emb_a, emb_b=emb_b)
    stats = {}
    result = _run(recall_intentions(
        vs, emb_a, user_ids=["u1"], limit=10, now=_NOW,
        dedup_threshold=0.85, stats_out=stats,
    ))
    assert [h["node_id"] for h in result] == [a.node_id]
    assert vs.delete_calls == [b.node_id]
    assert stats["dedup_threshold"] == 0.85


def test_explicit_threshold_wins_over_env(monkeypatch):
    monkeypatch.setenv("MEMORY_DEDUP_THRESHOLD", "0.5")
    emb_a = _unit([1.0, 0.0])
    emb_b = _unit([0.9, (1 - 0.81) ** 0.5])
    vs = _FakeVectorStore()
    _seed_project_pair(vs, emb_a=emb_a, emb_b=emb_b)
    result = _run(recall_intentions(
        vs, emb_a, user_ids=["u1"], limit=10, now=_NOW, dedup_threshold=0.95,
    ))
    assert len(result) == 2
    assert vs.delete_calls == []


def test_off_skips_embedding_backfill():
    vs = _FakeVectorStore()
    emb = _unit([1.0, 0.0])
    _seed_project_pair(vs, emb_a=emb, emb_b=list(emb))

    backfill_calls = []

    async def _get_embeddings(ids):
        backfill_calls.append(ids)
        return {}

    vs.get_embeddings = _get_embeddings
    for n in vs._nodes.values():
        n.embedding = None
    result = _run(recall_intentions(
        vs, emb, user_ids=["u1"], limit=10, now=_NOW, dedup_enabled=False,
    ))
    assert len(result) == 2
    assert backfill_calls == []


def test_invalid_threshold_degrades_to_env_default(monkeypatch):
    monkeypatch.delenv("MEMORY_DEDUP_THRESHOLD", raising=False)
    vs = _FakeVectorStore()
    emb = _unit([1.0, 0.0])
    a, b = _seed_project_pair(vs, emb_a=emb, emb_b=list(emb))
    stats = {}
    result = _run(recall_intentions(
        vs, emb, user_ids=["u1"], limit=10, now=_NOW, stats_out=stats,
        dedup_threshold="not-a-number",
    ))
    assert [h["node_id"] for h in result] == [a.node_id]
    assert stats["dedup_threshold"] == 0.95


# ============================================================================
# 写入侧批内判重
# ============================================================================


class _SameVectorEmbed:
    """所有文本同一向量：cosine=1，开启判重时必判为复本。"""

    async def embed_batch(self, texts):
        return [_unit([1.0, 0.0]) for _ in texts]

    async def embed_queued(self, text):
        return _unit([1.0, 0.0])


def _store_batch(config_recall):
    from hy_memory.pipelines.base import WriteRequest
    from hy_memory.pipelines.writer import MemoryWriter

    cfg = MemoryConfig.from_dict({"recall": config_recall})
    vs = _FakeVectorStore()
    writer = MemoryWriter(cfg, embed_service=_SameVectorEmbed(), vector_store=vs)
    req = WriteRequest(
        content="", messages=[], user_id="u1", agent_id="a1",
        session_id="s1", request_id=uuid.uuid4().hex,
    )
    intentions = [
        {"content": "A 项目每周一触发发布前校验流程", "tags": [], "valid_until": None},
        {"content": "B 项目每周一触发发布前校验流程", "tags": [], "valid_until": None},
    ]
    return _run(writer._store_intentions(intentions, req, vs, req.request_id)), vs


def test_write_batch_dedup_default_on_unchanged():
    ids, vs = _store_batch({})
    assert len(ids) == 1
    assert [n.content for n in vs._nodes.values()] == [
        "A 项目每周一触发发布前校验流程",
    ]


def test_write_batch_dedup_off_stores_both():
    ids, vs = _store_batch({"intention_dedup_enabled": False})
    assert len(ids) == 2
    assert len(vs._nodes) == 2


def test_read_threshold_does_not_change_write_batch_dedup(monkeypatch):
    """intention_dedup_threshold 只管读侧；写入侧仍取 MEMORY_DEDUP_THRESHOLD。"""
    monkeypatch.delenv("MEMORY_DEDUP_THRESHOLD", raising=False)
    ids, _ = _store_batch({"intention_dedup_threshold": 1.5})
    assert len(ids) == 1


# ============================================================================
# MemoryConfig
# ============================================================================


def test_config_defaults(monkeypatch):
    for k in (
        "MEMORY_INTENTION_DEDUP_ENABLED",
        "MEMORY_INTENTION_DEDUP_THRESHOLD",
        "MEMORY_DEDUP_THRESHOLD",
    ):
        monkeypatch.delenv(k, raising=False)
    cfg = MemoryConfig()
    assert cfg.recall.intention_dedup_enabled is True
    assert cfg.recall.intention_dedup_threshold == 0.95


def test_config_env_fallbacks(monkeypatch):
    monkeypatch.setenv("MEMORY_INTENTION_DEDUP_ENABLED", "false")
    monkeypatch.setenv("MEMORY_DEDUP_THRESHOLD", "0.9")
    monkeypatch.delenv("MEMORY_INTENTION_DEDUP_THRESHOLD", raising=False)
    cfg = MemoryConfig()
    assert cfg.recall.intention_dedup_enabled is False
    # 未设专用阈值 → 回退共享 MEMORY_DEDUP_THRESHOLD（兼容旧部署）
    assert cfg.recall.intention_dedup_threshold == 0.9

    monkeypatch.setenv("MEMORY_INTENTION_DEDUP_THRESHOLD", "0.8")
    assert MemoryConfig().recall.intention_dedup_threshold == 0.8


def test_config_from_dict_overrides_env(monkeypatch):
    monkeypatch.setenv("MEMORY_INTENTION_DEDUP_ENABLED", "true")
    cfg = MemoryConfig.from_dict({"recall": {
        "intention_dedup_enabled": False,
        "intention_dedup_threshold": 0.99,
    }})
    assert cfg.recall.intention_dedup_enabled is False
    assert cfg.recall.intention_dedup_threshold == 0.99
    assert cfg.to_dict()["recall"]["intention_dedup_enabled"] is False


# ============================================================================
# reader：按 client 配置，而不是调用时的 env
# ============================================================================


def _reader_for(enabled: bool, vs):
    from hy_memory.pipelines.reader_hybrid import HybridReadPipeline

    cfg = MemoryConfig.from_dict({
        "vector_store": {
            "provider": "chroma",
            "collection_name": f"t_intent_sw_{uuid.uuid4().hex[:8]}",
            "embedding_dims": 2,
        },
        "recall": {"intention_dedup_enabled": enabled},
    })
    reader = HybridReadPipeline(cfg, vector_store=vs)
    reader._vector_store = vs
    reader._vector_store_initialized = True
    return reader


async def _no_keyword_hits(**kw):
    return []


def _read_project_pair(enabled: bool):
    """经 reader 完整读路径召回 A/B 两条近似意图；返回 (返回的意图 id, 删库调用, a, b)。"""
    vs = _FakeVectorStore()
    vs.keyword_search = _no_keyword_hits
    emb = _unit([1.0, 0.0])
    a, b = _seed_project_pair(vs, emb_a=emb, emb_b=list(emb))
    # 读路径用真实 now：让两条都未过期且同一天
    today = datetime.now()
    for n in (a, b):
        n.memory_at = today
        n.valid_until = None
    reader = _reader_for(enabled, vs)
    _run(reader.initialize())
    resp = _run(reader.read(ReadRequest(
        query="发布前校验", query_embedding=emb,
        user_ids=["u1"], agent_ids=["a1"], limit=10, intention_limit=5,
    )))
    ids = {
        m["memory_id"] for m in resp.memories
        if m.get("layer") == "l7_intention"
    }
    return ids, list(vs.delete_calls), a, b


@pytest.mark.parametrize("env_value", ["true", "false"])
def test_reader_uses_per_client_config_not_env(monkeypatch, env_value):
    """调用时的 env 与 client 配置无关：两个 client 一开一关，各自生效。"""
    monkeypatch.setenv("MEMORY_INTENTION_DEDUP_ENABLED", env_value)
    monkeypatch.setenv("MEMORY_DEDUP_THRESHOLD", "0.0")

    ids_on, deleted_on, a, b = _read_project_pair(True)
    assert ids_on == {a.node_id}
    assert deleted_on == [b.node_id]

    ids_off, deleted_off, a, b = _read_project_pair(False)
    assert ids_off == {a.node_id, b.node_id}
    assert deleted_off == []


def test_reader_passes_client_threshold(monkeypatch):
    monkeypatch.setenv("MEMORY_DEDUP_THRESHOLD", "0.1")
    seen = []

    async def _spy(*args, **kwargs):
        seen.append(kwargs)
        return []

    monkeypatch.setattr("hy_memory.pipelines.reader_base.recall_intentions", _spy)
    vs = _FakeVectorStore()
    vs.keyword_search = _no_keyword_hits
    reader = _reader_for(True, vs)
    reader.config.recall.intention_dedup_threshold = 0.97
    _run(reader.initialize())
    _run(reader.read(ReadRequest(
        query="发布前校验", query_embedding=_unit([1.0, 0.0]),
        user_ids=["u1"], agent_ids=["a1"], limit=10, intention_limit=5,
    )))
    assert seen and seen[0]["dedup_enabled"] is True
    assert seen[0]["dedup_threshold"] == 0.97
