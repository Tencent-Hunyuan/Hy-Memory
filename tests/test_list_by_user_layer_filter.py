# -*- coding: utf-8 -*-
"""
回归：list_by_user 必须按 layers 过滤；evolution_chain 不得暴露 L1_RAW。

背景 bug（0.1.5.30）：
- faiss 后端 list_by_user 曾接受 layers 参数但未过滤，导致
  upsert_basic_profile._find_latest_l0(layers=[L0]) 拿到跨层节点（raw），
  新 L0 节点错误 supersede raw → search 的 evolution_chain 泄漏 raw。
"""

import asyncio
import uuid

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.models.memory import MemoryNode, MemoryLayer, MemoryStatus


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


DIMS = 32


def _node(user_id, layer, content="x", is_latest=True):
    import random
    n = MemoryNode(
        node_id=str(uuid.uuid4()),
        user_id=user_id,
        agent_id="a1",
        session_id="s1",
        content=content,
        layer=layer,
        status=MemoryStatus.ACTIVE,
        is_latest=is_latest,
    )
    vec = [random.gauss(0, 1) for _ in range(DIMS)]
    norm = sum(v * v for v in vec) ** 0.5
    n.embedding = [v / norm for v in vec]
    return n


@pytest.fixture
def faiss_store(tmp_path):
    config = MemoryConfig.from_dict({
        "vector_store": {
            "provider": "faiss",
            "collection_name": "test_layer_filter",
            "persist_directory": str(tmp_path / "faiss_data"),
            "embedding_dims": DIMS,
        },
    })
    from hy_memory.data.vector_store_faiss import FaissVectorStore
    vs = FaissVectorStore(config)
    run(vs.initialize())
    yield vs
    run(vs.close())


class TestListByUserLayerFilter:
    def test_faiss_list_by_user_filters_layers(self, faiss_store):
        u = "user_layer_test"
        run(faiss_store.upsert_batch([
            _node(u, MemoryLayer.L1_RAW, "raw dialogue"),
            _node(u, MemoryLayer.L0_BASIC_INFO, "The user's name is ivanye."),
            _node(u, MemoryLayer.L2_FACT, "likes badminton"),
        ]))

        # 只要 L0：绝不能混入 raw
        l0 = run(faiss_store.list_by_user(user_id=u, layers=[MemoryLayer.L0_BASIC_INFO]))
        assert len(l0) == 1
        assert l0[0].layer == MemoryLayer.L0_BASIC_INFO

        # 多层
        multi = run(faiss_store.list_by_user(
            user_id=u, layers=[MemoryLayer.L0_BASIC_INFO, MemoryLayer.L2_FACT]
        ))
        got = {n.layer for n in multi}
        assert MemoryLayer.L1_RAW not in got
        assert got == {MemoryLayer.L0_BASIC_INFO, MemoryLayer.L2_FACT}

        # 不传 layers：返回除 raw 外的全部（raw 对外读路径不可见）
        all_nodes = run(faiss_store.list_by_user(user_id=u))
        assert len(all_nodes) == 2
        assert MemoryLayer.L1_RAW not in {n.layer for n in all_nodes}

        # 只点名 raw：拿不到（不是"以调用方为准"，是一律挡掉）
        only_raw = run(faiss_store.list_by_user(user_id=u, layers=[MemoryLayer.L1_RAW]))
        assert only_raw == []

        # 内部管线显式放行才拿得到（writer 拼对话历史依赖此路径）
        internal = run(faiss_store.list_by_user(
            user_id=u, layers=[MemoryLayer.L1_RAW], allow_raw_layer=True,
        ))
        assert len(internal) == 1
        assert internal[0].layer == MemoryLayer.L1_RAW

        # 放行且不传 layers：回到"全部层级"语义（clone 依赖）
        internal_all = run(faiss_store.list_by_user(user_id=u, allow_raw_layer=True))
        assert len(internal_all) == 3


class TestRawOnlyFilterReturnsEmptyCrossBackend:
    """回归 #189：Chroma/Qdrant 的 raw-only 过滤此前误返回所有非 raw 层。

    修复后，`layers=[L1_RAW]` 且 `allow_raw_layer=False` 应在查询 backend
    之前就短路返回空列表。这里只构造 store 对象（不 initialize、不依赖
    chromadb/qdrant_client 可用），验证短路分支本身的正确性；完整跨后端
    集成覆盖在 CI 有 backend 依赖时由同一断言驱动。
    """

    @pytest.mark.parametrize("provider,cls", [
        ("chroma", "ChromaVectorStore"),
        ("qdrant", "QdrantVectorStore"),
    ])
    def test_raw_only_filter_returns_empty_without_backend(self, provider, cls):
        import importlib
        mod = importlib.import_module(f"hy_memory.data.vector_store_{provider}")
        store_cls = getattr(mod, cls)
        config = MemoryConfig.from_dict({
            "vector_store": {
                "provider": provider,
                "collection_name": "test_raw_only",
                "embedding_dims": DIMS,
            },
        })
        vs = store_cls(config)

        # 只点名 raw 且不允许 raw：必须返回空列表，而不是退回非 raw 层
        nodes = run(vs.list_by_user(
            user_id="u", layers=[MemoryLayer.L1_RAW], allow_raw_layer=False,
        ))
        assert nodes == []

    @pytest.mark.parametrize("provider,cls,module", [
        ("chroma", "ChromaVectorStore", "chromadb"),
        ("qdrant", "QdrantVectorStore", "qdrant_client"),
    ])
    def test_layer_filters_against_real_backend(self, tmp_path, provider, cls, module):
        """Real local backend: raw-only -> empty, mixed -> only the non-raw layers."""
        pytest.importorskip(module)
        import importlib
        mod = importlib.import_module(f"hy_memory.data.vector_store_{provider}")
        store_cls = getattr(mod, cls)
        config = MemoryConfig.from_dict({
            "vector_store": {
                "provider": provider,
                "collection_name": "test_raw_only_real",
                "persist_directory": str(tmp_path / f"{provider}_data"),
                "embedding_dims": DIMS,
            },
        })
        vs = store_cls(config)
        run(vs.initialize())
        try:
            u = "user_raw_only_real"
            run(vs.upsert_batch([
                _node(u, MemoryLayer.L1_RAW, "raw dialogue"),
                _node(u, MemoryLayer.L0_BASIC_INFO, "The user's name is ivanye."),
                _node(u, MemoryLayer.L2_FACT, "likes badminton"),
            ]))

            # raw-only with raw disabled: empty, not "every non-raw layer" (#189)
            only_raw = run(vs.list_by_user(
                user_id=u, layers=[MemoryLayer.L1_RAW], allow_raw_layer=False,
            ))
            assert only_raw == []

            # mixed raw + fact with raw disabled: raw dropped, fact kept, L0 not leaked
            mixed = run(vs.list_by_user(
                user_id=u,
                layers=[MemoryLayer.L1_RAW, MemoryLayer.L2_FACT],
                allow_raw_layer=False,
            ))
            assert [n.layer for n in mixed] == [MemoryLayer.L2_FACT]

            # raw explicitly allowed: the raw node is reachable
            internal = run(vs.list_by_user(
                user_id=u, layers=[MemoryLayer.L1_RAW], allow_raw_layer=True,
            ))
            assert [n.layer for n in internal] == [MemoryLayer.L1_RAW]
        finally:
            run(vs.close())


class TestEvolutionChainExcludesRaw:
    def test_chain_drops_raw_nodes(self):
        from hy_memory.pipelines._retrieval.evolution import _expand_one_chain

        raw = _node("u", MemoryLayer.L1_RAW, "你是一个AI能力中台", is_latest=False)
        head = _node("u", MemoryLayer.L0_BASIC_INFO, "The user's name is ivanye.", is_latest=True)
        head.supersedes = [raw.node_id]
        raw.superseded_by = [head.node_id]

        class _FakeVS:
            async def get_by_ids(self, ids):
                m = {raw.node_id: raw, head.node_id: head}
                return [m[i] for i in ids if i in m]

        exp = run(_expand_one_chain(_FakeVS(), {"node": head, "score": 0.9}))
        chain = exp.get("evolution_chain", [])
        # 链头保留，raw 被过滤
        layers = [c["layer"] for c in chain]
        assert "l1_raw" not in layers
        assert any(c["node_id"] == head.node_id for c in chain)
        assert all(c["node_id"] != raw.node_id for c in chain)


class TestFindLatestL0Validates:
    """_find_latest_l0 必须只认合法 L0 链头（layer + kv 结构），脏节点不当链头。"""

    def test_raw_node_not_treated_as_l0_head(self):
        from hy_memory.agent.tools.basic_profile import _find_latest_l0, _is_valid_l0_head

        # 一个 is_latest 的 raw 节点（无 basic_info_kv），不应被当作 L0 链头
        raw = _node("u", MemoryLayer.L1_RAW, "你是一个AI能力中台", is_latest=True)
        assert _is_valid_l0_head(raw) is False

        # 合法 L0 节点
        l0 = _node("u", MemoryLayer.L0_BASIC_INFO, "The user's name is ivanye.", is_latest=True)
        l0.custom = {"basic_info_kv": {"name": "ivanye"}}
        assert _is_valid_l0_head(l0) is True

        class _FakeVS:
            def __init__(self, nodes):
                self._nodes = nodes
            async def list_by_user(self, **kwargs):
                # 模拟后端 layer 过滤失效：把 raw 也返回
                return self._nodes
            async def get_by_id(self, _id):
                return None

        # 库里只有一个 raw（layers 过滤失效场景）→ 不能当链头，返回 None
        head = run(_find_latest_l0(_FakeVS([raw]), "u"))
        assert head is None

        # 库里有 raw + 合法 L0 → 必须挑出 L0，绝不返回 raw
        head2 = run(_find_latest_l0(_FakeVS([raw, l0]), "u"))
        assert head2 is not None
        assert head2.node_id == l0.node_id
