"""层级包成员池截断：正交成员（相似度 0.0）不能被更差的成员挤掉。

`build_hierarchy_bundle_candidate` 用 `-(cosine(...) or -1.0)` 给成员排序。
`cosine()` 用 None 表示「缺向量/维度不符」，但 `0.0 or -1.0` 会把合法的 0.0
折成 -1.0，取负后变成 +1.0 —— 正交成员排到了负相似度成员之后，在
`[:per_node_pool]` 截断时优先被丢掉，留下的是相关性更差的那些。
"""

import asyncio
from types import SimpleNamespace

from hy_memory.pipelines._retrieval.hierarchy_bundle import (
    build_hierarchy_bundle_candidate,
)


class _Graph:
    def __init__(self, topics, memberships):
        self._topics = topics
        self._memberships = memberships

    async def get_hierarchy_topics(self, isolation_key):
        return self._topics

    async def get_hierarchy_memberships(self, isolation_key):
        return self._memberships


class _Store:
    def __init__(self, embeddings):
        self._embeddings = embeddings
        self.requested = None

    async def get_embeddings(self, ids):
        return {i: self._embeddings[i] for i in ids if i in self._embeddings}

    async def get_by_ids(self, ids):
        self.requested = list(ids)
        return [
            SimpleNamespace(
                node_id=i, content=f"content of {i}", custom={}, tags=[], layer="L2",
            )
            for i in ids if i in self._embeddings
        ]


async def _build(embeddings, member_pool):
    topics = [{
        "topic_id": "t1", "subject": "alpha", "parent_id": "",
        "path": ["alpha"], "embedding": [1.0, 0.0],
        "node_type": "facet", "node_types": ["facet"],
    }]
    memberships = [{"node_id": name, "topic_id": "t1"} for name in embeddings]
    store = _Store(embeddings)

    async def run_scorer(scorer, query, documents):
        return [0.5] * len(documents)

    await build_hierarchy_bundle_candidate(
        graph_store=_Graph(topics, memberships),
        vector_store=store,
        isolation_key="u::a",
        query="alpha",
        query_embedding=[1.0, 0.0],
        query_subjects=["alpha"],
        anchor_ids=(),
        scorer=lambda query, documents: [0.5] * len(documents),
        text_fn=lambda item: item["content"],
        run_scorer=run_scorer,
        topic_limit=4,
        node_beam_limit=2,
        member_pool=member_pool,
        max_members=member_pool,
        token_budget=10000,
    )
    return store.requested


def test_orthogonal_member_beats_a_negative_one_in_the_pool():
    """cos=0.0 的成员比 cos=-0.2 的更好，池子只留两个时不能丢它。"""
    embeddings = {
        "m_pos": [1.0, 0.0],        # cos = 1.0
        "m_zero": [0.0, 1.0],       # cos = 0.0  —— 正交
        "m_neg": [-0.2, 0.9798],    # cos ≈ -0.2
    }

    requested = asyncio.run(_build(embeddings, member_pool=2))

    assert "m_pos" in requested
    assert "m_zero" in requested
    assert "m_neg" not in requested


def test_member_without_an_embedding_still_sorts_last():
    """缺向量的成员（cosine 返回 None）仍应排在最后，不能被上面的修改带走。"""
    embeddings = {
        "m_pos": [1.0, 0.0],        # cos = 1.0
        "m_neg": [-0.2, 0.9798],    # cos ≈ -0.2
        "m_missing": [0.0, 0.0],    # 零向量 -> cosine 返回 None
    }

    requested = asyncio.run(_build(embeddings, member_pool=2))

    assert "m_pos" in requested
    assert "m_neg" in requested
    assert "m_missing" not in requested
