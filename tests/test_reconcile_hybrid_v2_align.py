# -*- coding: utf-8 -*-
"""reconcile 候选召回：additive + tag/entity boost。"""

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus
from hy_memory.pipelines._retrieval.reconcile_retrieval import (
    ReconcileHybridRetriever,
    ReconcileRetrievalConfig,
)
from hy_memory.pipelines._retrieval.scoring import score_vdb_node_additive


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class _FakeVS:
    def __init__(self, nodes: List[MemoryNode], sem_scores: Dict[str, float]):
        self._nodes = {n.node_id: n for n in nodes}
        self._sem = sem_scores
        self.keyword_score_normalized = True

    async def search(self, **kw):
        out = []
        for nid, sc in self._sem.items():
            n = self._nodes.get(nid)
            if n:
                out.append({"node_id": nid, "node": n, "score": sc})
        out.sort(key=lambda x: x["score"], reverse=True)
        return out[: kw.get("limit", 20)]

    async def keyword_search(self, **kw):
        return []

    async def get_by_ids(self, ids):
        return [self._nodes[i] for i in ids if i in self._nodes]


class _BatchOnlyVS(_FakeVS):
    def __init__(self, nodes: List[MemoryNode], sem_scores: Dict[str, float]):
        super().__init__(nodes, sem_scores)
        self.dense_batch_calls = 0
        self.keyword_batch_calls = 0

    async def search(self, **kw):
        raise AssertionError("batch reconcile must not call scalar dense search")

    async def keyword_search(self, **kw):
        raise AssertionError("batch reconcile must not call scalar keyword search")

    async def search_batch(self, requests):
        self.dense_batch_calls += 1
        rows = []
        for request in requests:
            row = []
            for nid, score in self._sem.items():
                node = self._nodes[nid]
                row.append({"node_id": nid, "node": node, "score": score})
            rows.append(row[: request.get("limit", 20)])
        return rows

    async def keyword_search_batch(self, requests):
        self.keyword_batch_calls += 1
        return [[] for _ in requests]


class _FakeGraph:
    def __init__(self, hits: List[Dict[str, Any]]):
        self._hits = hits

    async def find_memories_by_entities(self, isolation_key, entities, limit=100):
        return list(self._hits)


class _Embed:
    async def embed_batch(self, texts):
        # 正交单位向量，保证同文 cos=1
        out = []
        for i, _t in enumerate(texts):
            v = [0.0] * 8
            v[i % 8] = 1.0
            out.append(v)
        return out


def _node(nid, content, tags=None):
    n = MemoryNode(
        node_id=nid, user_id="u1", agent_id="a1", session_id="s1",
        layer=MemoryLayer.L2_FACT, content=content,
        tags=list(tags or []),
    )
    n.status = MemoryStatus.ACTIVE
    return n


@pytest.mark.asyncio
async def test_reconcile_additive_keeps_full_sem_without_bm25():
    """无 BM25 时 sem 不再被 ×0.6。"""
    hi = _node("hi", "generic note about hobbies")
    vs = _FakeVS([hi], {"hi": 0.55})
    ret = ReconcileHybridRetriever(
        ReconcileRetrievalConfig(limit=10, min_score=0.4, w_bm25=0.2, w_tag=0.3),
    )
    hits = await ret.search_candidates(
        "generic note about hobbies",
        [1.0] + [0.0] * 31,
        vector_store=vs,
        user_id="u1",
        agent_id="a1",
        layers=[MemoryLayer.L2_FACT],
    )
    assert hits
    # additive: ≈0.55 (+ tiny bm25 if any)
    assert hits[0]["score"] >= 0.55 - 1e-6
    assert hits[0]["_sem"] == pytest.approx(0.55)


@pytest.mark.asyncio
async def test_reconcile_entity_boost_lifts_low_sem(monkeypatch):
    """sem 偏低但 Graph Entity 命中 → 可过 min_score。"""
    from hy_memory.pipelines._retrieval import reconcile_retrieval

    monkeypatch.setattr(
        reconcile_retrieval,
        "extract_entities",
        lambda *_args, **_kwargs: [("Tokyo", "GPE")],
    )
    tokyo = _node("t1", "I visited Tokyo last summer.", tags=["travel"])
    # sem 仅 0.25，旧 convex 0.6*0.25=0.15；additive + entity 0.3 → 0.55
    vs = _FakeVS([tokyo], {"t1": 0.25})
    gs = _FakeGraph([{"node_id": "t1", "match_score": 1.0, "entity_name": "Tokyo"}])
    ret = ReconcileHybridRetriever(
        ReconcileRetrievalConfig(limit=10, min_score=0.4, w_bm25=0.2, w_tag=0.3),
    )
    hits = await ret.search_candidates(
        "What did I do in Tokyo?",
        [1.0] + [0.0] * 31,
        vector_store=vs,
        user_id="u1",
        agent_id="a1",
        layers=[MemoryLayer.L2_FACT],
        embed_service=_Embed(),
        graph_store=gs,
    )
    assert any(h["node"].node_id == "t1" for h in hits)
    hit = next(h for h in hits if h["node"].node_id == "t1")
    assert hit["_entity_boost"] == pytest.approx(1.0)
    expected = score_vdb_node_additive(0.25, bm25_score=hit["_bm25"], tag_cos=1.0, w_tag=0.3)
    assert hit["score"] == pytest.approx(expected, abs=1e-4)
    assert hit["score"] >= 0.4


def test_config_from_env_weights():
    cfg = ReconcileRetrievalConfig.from_env(limit=15, min_score=0.35)
    assert cfg.limit == 15
    assert cfg.min_score == 0.35
    assert cfg.w_bm25 == pytest.approx(0.2)
    assert cfg.w_tag == pytest.approx(0.3)


@pytest.mark.asyncio
async def test_reconcile_batch_collapses_transport_and_preserves_query_rows(monkeypatch):
    async def direct_to_thread(func, /, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", direct_to_thread)
    node = _node("n1", "The user enjoys painting landscapes.")
    vs = _BatchOnlyVS([node], {"n1": 0.8})
    ret = ReconcileHybridRetriever(
        ReconcileRetrievalConfig(limit=10, min_score=0.4),
    )
    monkeypatch.setattr(
        "hy_memory.pipelines._retrieval.reconcile_retrieval.extract_entities",
        lambda *_args, **_kwargs: [],
    )

    rows = await ret.search_candidates_batch(
        ["painting hobby", "landscape art", "weekend painting"],
        [[1.0, 0.0], [0.9, 0.1], [0.8, 0.2]],
        vector_store=vs,
        user_id="u1",
        agent_id="a1",
        layers=[MemoryLayer.L2_FACT],
    )

    assert vs.dense_batch_calls == 1
    assert vs.keyword_batch_calls == 1
    assert len(rows) == 3
    assert [[hit["node"].node_id for hit in row] for row in rows] == [
        ["n1"], ["n1"], ["n1"],
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["scalar", "batch", "fallback"])
async def test_reconcile_preserves_raw_queries_per_row(monkeypatch, transport):
    queries = ["C++", "src/foo_bar.py"]
    normalized = {"C++": "", "src/foo_bar.py": "src foo bar py"}
    monkeypatch.setattr(
        "hy_memory.pipelines._retrieval.reconcile_retrieval.lemmatize_for_bm25",
        lambda text: normalized[text],
    )
    requests = []
    vs = _FakeVS([], {})

    async def keyword_search(**request):
        requests.append(request)
        return []

    vs.keyword_search = keyword_search
    if transport == "batch":
        async def keyword_search_batch(batch):
            requests.extend(batch)
            return [[] for _ in batch]

        vs.keyword_search_batch = keyword_search_batch

    retriever = ReconcileHybridRetriever(ReconcileRetrievalConfig())
    options = {
        "vector_store": vs,
        "user_id": "u1",
        "agent_id": "a1",
        "layers": [MemoryLayer.L2_FACT],
        "include_sources": ["imported"],
    }
    if transport == "scalar":
        for query in queries:
            assert await retriever.search_candidates(query, [1.0, 0.0], **options) == []
    else:
        rows = await retriever.search_candidates_batch(
            queries, [[1.0, 0.0], [0.0, 1.0]], **options,
        )
        assert rows == [[], []]

    assert [(request["raw_query"], request["query"]) for request in requests] == [
        ("C++", ""), ("src/foo_bar.py", "src foo bar py"),
    ]
    assert all(request["user_id"] == "u1" for request in requests)
    assert all(request["agent_ids"] is None for request in requests)
    assert all(request["include_sources"] == ["imported"] for request in requests)
