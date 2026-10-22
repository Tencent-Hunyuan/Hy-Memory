# -*- coding: utf-8 -*-
"""
list 链路的 layers 服务端筛选（R3 需求：CTX 需按 layer / category 只列某几类）。

底层 list_by_user 早已支持 layers，缺的是 client 层未透传。本文件覆盖透传后的语义：

- 不传 layers 时必须与改动前逐字一致（返回 L1_RAW 以外的全部层级），
  这是现网 list 的既有口径，不得因新增参数而改变。
- 传了 layers 则完全以调用方为准，包括「只要 L1_RAW」这种显式请求。
- 指定层级全不在图库（L6/L7）时跳过图库查询——连接本身有开销，pro 模式下更是白连。
"""

import asyncio
import uuid

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.client import HyMemoryClient
from hy_memory.models.memory import MemoryNode, MemoryLayer, MemoryStatus


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


DIMS = 32


def _node(
    user_id="u1",
    content="x",
    layer=MemoryLayer.L2_FACT,
    status=MemoryStatus.ACTIVE,
    session_id="s1",
    source="default",
):
    import random
    n = MemoryNode(
        node_id=str(uuid.uuid4()),
        user_id=user_id,
        agent_id="a1",
        session_id=session_id,
        source=source,
        content=content,
        layer=layer,
        status=status,
        is_latest=status == MemoryStatus.ACTIVE,
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


def _client_with(store):
    c = HyMemoryClient.__new__(HyMemoryClient)
    c._vector_store = store
    return c


class TestNormalizeLayers:
    """入参规范化：接受字符串是为了与 search 侧（List[str]）对齐。"""

    def test_none_and_empty_return_none(self):
        assert HyMemoryClient._normalize_layers(None) is None
        assert HyMemoryClient._normalize_layers([]) is None

    def test_string_converted_to_enum(self):
        got = HyMemoryClient._normalize_layers(["l2_fact", "l4_identity"])
        assert got == [MemoryLayer.L2_FACT, MemoryLayer.L4_IDENTITY]

    def test_enum_passthrough_and_mixed(self):
        got = HyMemoryClient._normalize_layers([MemoryLayer.L2_FACT, "l3_summary"])
        assert got == [MemoryLayer.L2_FACT, MemoryLayer.L3_SUMMARY]

    def test_legacy_alias_mapped(self):
        """需求文档里写的是旧编号 l5_schema，实际应落到 l6_schema。"""
        assert HyMemoryClient._normalize_layers(["l5_schema"]) == [MemoryLayer.L6_SCHEMA]

    def test_dedup_preserves_order(self):
        got = HyMemoryClient._normalize_layers(["l2_fact", "l2_fact", "l1_raw"])
        assert got == [MemoryLayer.L2_FACT, MemoryLayer.L1_RAW]

    def test_invalid_raises_not_silently_ignored(self):
        """静默忽略非法值会让调用方以为筛选生效，实际拿到全量——必须报错。

        l3_identity 是需求文档里的笔误（L3 实为 summary），正好作为用例。
        """
        with pytest.raises(ValueError):
            HyMemoryClient._normalize_layers(["l3_identity"])


class TestNormalizeStatuses:
    def test_none_and_empty_preserve_active_default(self):
        assert HyMemoryClient._normalize_statuses(None) is None
        assert HyMemoryClient._normalize_statuses([]) is None

    def test_string_and_enum_values_are_deduplicated(self):
        got = HyMemoryClient._normalize_statuses(
            ["superseded", MemoryStatus.SHADOW, "superseded"]
        )
        assert got == [MemoryStatus.SUPERSEDED, MemoryStatus.SHADOW]

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError):
            HyMemoryClient._normalize_statuses(["deleted"])


class TestVdbBucketLayerFilter:
    def _seed(self, store):
        run(store.upsert_batch([
            _node(content="fact", layer=MemoryLayer.L2_FACT),
            _node(content="summary", layer=MemoryLayer.L3_SUMMARY),
            _node(content="identity", layer=MemoryLayer.L4_IDENTITY),
            _node(content="raw", layer=MemoryLayer.L1_RAW),
        ]))

    def test_no_layers_excludes_raw_as_before(self, faiss_store):
        """不传时口径不变：L1_RAW 不出现在 list 中。"""
        self._seed(faiss_store)
        got = run(_client_with(faiss_store)._list_vdb_bucket(
            user_id="u1", agent_id=None, limit=100, offset=0, order="desc",
        ))
        assert {m["content"] for m in got["memories"]} == {"fact", "summary", "identity"}

    def test_single_layer_filters(self, faiss_store):
        self._seed(faiss_store)
        got = run(_client_with(faiss_store)._list_vdb_bucket(
            user_id="u1", agent_id=None, limit=100, offset=0, order="desc",
            layers=[MemoryLayer.L2_FACT],
        ))
        assert [m["content"] for m in got["memories"]] == ["fact"]
        assert got["total"] == 1

    def test_multi_layer_filters(self, faiss_store):
        self._seed(faiss_store)
        got = run(_client_with(faiss_store)._list_vdb_bucket(
            user_id="u1", agent_id=None, limit=100, offset=0, order="desc",
            layers=[MemoryLayer.L2_FACT, MemoryLayer.L3_SUMMARY],
        ))
        assert {m["content"] for m in got["memories"]} == {"fact", "summary"}

    def test_explicit_raw_returns_raw(self, faiss_store):
        """默认列表不含 raw；调用方点名 L1_RAW 时按统一 list 合同返回。"""
        self._seed(faiss_store)
        got = run(_client_with(faiss_store)._list_vdb_bucket(
            user_id="u1", agent_id=None, limit=100, offset=0, order="desc",
            layers=[MemoryLayer.L1_RAW],
        ))
        assert [m["content"] for m in got["memories"]] == ["raw"]
        assert got["memories"][0]["layer"] == MemoryLayer.L1_RAW.value
        assert got["total"] == 1

    def test_raw_mixed_with_other_layers_returns_both(self, faiss_store):
        self._seed(faiss_store)
        got = run(_client_with(faiss_store)._list_vdb_bucket(
            user_id="u1", agent_id=None, limit=100, offset=0, order="desc",
            layers=[MemoryLayer.L1_RAW, MemoryLayer.L2_FACT],
        ))
        assert {m["content"] for m in got["memories"]} == {"raw", "fact"}

    def test_raw_uses_generic_session_and_source_filters(self, faiss_store):
        run(faiss_store.upsert_batch([
            _node(
                content="cursor raw",
                layer=MemoryLayer.L1_RAW,
                session_id="s1",
                source="cursor",
            ),
            _node(
                content="claude raw",
                layer=MemoryLayer.L1_RAW,
                session_id="s2",
                source="claude",
            ),
        ]))
        got = run(_client_with(faiss_store)._list_vdb_bucket(
            user_id="u1",
            agent_id=None,
            limit=100,
            offset=0,
            order="desc",
            layers=[MemoryLayer.L1_RAW],
            session_ids=["s2"],
            include_sources=["claude"],
            exclude_sources=["cursor"],
        ))
        assert [item["content"] for item in got["memories"]] == ["claude raw"]
        assert got["memories"][0]["source"] == "claude"

    def test_status_filter_defaults_to_active_and_can_read_superseded(self, faiss_store):
        run(faiss_store.upsert_batch([
            _node(content="active", status=MemoryStatus.ACTIVE),
            _node(content="old", status=MemoryStatus.SUPERSEDED),
            _node(content="hidden", status=MemoryStatus.SHADOW),
        ]))

        default_page = run(_client_with(faiss_store)._list_vdb_bucket(
            user_id="u1", agent_id=None, limit=100, offset=0, order="desc",
        ))
        assert [m["content"] for m in default_page["memories"]] == ["active"]

        lifecycle_page = run(_client_with(faiss_store)._list_vdb_bucket(
            user_id="u1", agent_id=None, limit=100, offset=0, order="desc",
            statuses=[MemoryStatus.SUPERSEDED, MemoryStatus.SHADOW],
        ))
        assert {m["content"] for m in lifecycle_page["memories"]} == {"old", "hidden"}

    def test_layer_filter_composes_with_multi_user(self, faiss_store):
        """R3 与 R1 叠加：多用户 + 层级筛选同时生效。"""
        run(faiss_store.upsert_batch([
            _node("u1", content="u1 fact", layer=MemoryLayer.L2_FACT),
            _node("u1", content="u1 summary", layer=MemoryLayer.L3_SUMMARY),
            _node("u2", content="u2 fact", layer=MemoryLayer.L2_FACT),
            _node("u3", content="u3 fact", layer=MemoryLayer.L2_FACT),
        ]))
        got = run(_client_with(faiss_store)._list_vdb_bucket(
            user_id="", agent_id=None, user_ids=["u1", "u2"], agent_ids=None,
            limit=100, offset=0, order="desc", layers=[MemoryLayer.L2_FACT],
        ))
        assert {m["content"] for m in got["memories"]} == {"u1 fact", "u2 fact"}


class TestGraphBucketLayerSkip:
    """图库连接有开销，指定的层级都不在图库时不该连。"""

    def _client_recording_graph_access(self):
        c = HyMemoryClient.__new__(HyMemoryClient)
        calls = []

        async def _acquire():
            calls.append(1)
            return None, False

        c._acquire_graph_store_for_purge = _acquire
        return c, calls

    def test_vdb_only_layers_skip_graph_store(self):
        c, calls = self._client_recording_graph_access()
        got = run(c._list_graph_bucket(
            user_id="u1", agent_id=None, limit=10, offset=0, order="desc",
            layers=[MemoryLayer.L2_FACT],
        ))
        assert got is None
        assert calls == [], "指定层级全在 VDB 侧时不应连图库"

    def test_graph_layer_still_queries(self):
        c, calls = self._client_recording_graph_access()
        run(c._list_graph_bucket(
            user_id="u1", agent_id=None, limit=10, offset=0, order="desc",
            layers=[MemoryLayer.L7_INTENTION],
        ))
        assert calls == [1], "指定了图层就必须查图库"

    def test_no_layers_still_queries(self):
        c, calls = self._client_recording_graph_access()
        run(c._list_graph_bucket(
            user_id="u1", agent_id=None, limit=10, offset=0, order="desc",
        ))
        assert calls == [1], "不传 layers 时行为与改动前一致"

    def test_graph_status_filter_matches_vdb_contract(self):
        active = _node(content="active graph", layer=MemoryLayer.L6_SCHEMA)
        old = _node(
            content="old graph",
            layer=MemoryLayer.L6_SCHEMA,
            status=MemoryStatus.SUPERSEDED,
        )

        class GraphStore:
            async def get_all_nodes(self, *_args, **_kwargs):
                return [active, old]

            async def close(self):
                return None

        c = HyMemoryClient.__new__(HyMemoryClient)

        async def _acquire():
            return GraphStore(), True

        c._acquire_graph_store_for_purge = _acquire
        default_page = run(c._list_graph_bucket(
            user_id="u1", agent_id=None, limit=10, offset=0, order="desc",
            layers=[MemoryLayer.L6_SCHEMA],
        ))
        assert [item["content"] for item in default_page["nodes"]] == ["active graph"]

        old_page = run(c._list_graph_bucket(
            user_id="u1", agent_id=None, limit=10, offset=0, order="desc",
            layers=[MemoryLayer.L6_SCHEMA],
            statuses=[MemoryStatus.SUPERSEDED],
        ))
        assert [item["content"] for item in old_page["nodes"]] == ["old graph"]
