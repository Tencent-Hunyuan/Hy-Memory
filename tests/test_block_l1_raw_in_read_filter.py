# -*- coding: utf-8 -*-
"""
L1_RAW 对外读路径不可见：闸门焊在向量库唯一查询入口。

方案背景（2026-08-31）：
业务方通过 search / list / keyword_search 能搜到原始对话。此前的做法是
把 raw 一并从库里捞回进程、再用 Python 筛掉（`client.py` 旧 _list_vdb_bucket），
属于"查询后过滤"：raw 出过库、进过本进程内存。

现改为把排除条件拼进 filter 随请求下发给 vdb，raw 不出库。闸门焊在
`_build_filter_expr` 上 —— 它是 search / keyword_search /
hybrid_search_native / list_by_user 四个查询方法的共同必经处，
上层无法绕开，新增接口自动受保护。

按 ID 直取是精确读取：raw ID 返回 L1_RAW，fact ID 返回对应事实。权限由
调用 SDK 前的产品边界检查，不由 SDK 根据 layer 猜测。

内部管线需读原始对话时显式 allow_raw_layer=True 放行：
- `writer._get_recent_dialogue_history`：提取前拼多轮对话历史喂 LLM
- `client.async_clone_user_memories`：全量复制含 raw
"""

import asyncio
import uuid

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.models.memory import MemoryNode, MemoryLayer, MemoryStatus

RAW = MemoryLayer.L1_RAW.value


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class TestFaissBackendBlocksRaw:
    """faiss 后端（测试与本地开发用）行为一致。"""

    @pytest.fixture
    def store(self, tmp_path):
        from hy_memory.data.vector_store_faiss import FaissVectorStore
        # persist_directory 必须指到 tmp_path：写错键名会落到
        # ~/.hy_memory 真实数据目录，污染本地数据且用例互相干扰。
        cfg = MemoryConfig.from_dict({
            "vector_store": {
                "provider": "faiss",
                "collection_name": "test_block_raw",
                "persist_directory": str(tmp_path / "faiss_data"),
                "embedding_dims": 32,
            },
        })
        s = FaissVectorStore(cfg)
        run(s.initialize())
        run(s.upsert_batch([
            self._node("u1", MemoryLayer.L1_RAW, "原始对话"),
            self._node("u1", MemoryLayer.L2_FACT, "事实"),
        ]))
        yield s

    @staticmethod
    def _node(user_id, layer, content):
        import random
        return MemoryNode(
            node_id=str(uuid.uuid4()),
            user_id=user_id, agent_id="a1", session_id="s1",
            content=content, layer=layer,
            status=MemoryStatus.ACTIVE, is_latest=True,
            embedding=[random.random() for _ in range(32)],
        )

    def test_default_blocks_raw(self, store):
        got = run(store.list_by_user(user_id="u1"))
        assert {n.layer for n in got} == {MemoryLayer.L2_FACT}

    def test_only_raw_returns_empty(self, store):
        got = run(store.list_by_user(user_id="u1", layers=[MemoryLayer.L1_RAW]))
        assert got == []

    def test_allow_raw_returns_it(self, store):
        got = run(store.list_by_user(
            user_id="u1", layers=[MemoryLayer.L1_RAW], allow_raw_layer=True,
        ))
        assert [n.layer for n in got] == [MemoryLayer.L1_RAW]


class TestInternalPipelinesStillReadRaw:
    """内部放行点：漏掉任一处都会造成功能退化，故逐处断言。"""

    def test_writer_dialogue_history_allows_raw(self):
        """提取前拼多轮对话历史必须能读到 raw，否则提取质量下降。"""
        import inspect
        from hy_memory.pipelines.writer import MemoryWriter
        src = inspect.getsource(MemoryWriter._get_recent_history)
        assert "allow_raw_layer=True" in src, \
            "writer 拼对话历史未放行 raw，会拿不到历史、降低提取质量"

    def test_clone_allows_raw(self):
        """clone 语义是完整复制，含 raw；漏掉会让新用户丢失全部对话历史。"""
        import inspect
        from hy_memory.client import HyMemoryClient
        src = inspect.getsource(HyMemoryClient.async_clone_user)
        assert src.count("allow_raw_layer=True") >= 2, \
            "clone 的判空与全量列举都需放行 raw"


class TestListNoLongerFiltersInProcess:
    """回归：list 不得再把 raw 捞回进程后用 Python 筛掉。

    翔哥明确反对"查询后过滤"。若这句 Python 过滤被复原，
    raw 会重新经过本进程内存 —— 用源码断言防止回退。
    """

    def test_no_python_side_raw_filter(self):
        import inspect
        from hy_memory.client import HyMemoryClient
        src = inspect.getsource(HyMemoryClient._list_vdb_bucket)
        assert "n.layer != MemoryLayer.L1_RAW" not in src, \
            "list 又出现进程内过滤 raw 的写法，应交由 vdb 侧 filter 完成"


class TestGetByIdReturnsExactLayer:
    """按 ID 直取返回命中的真实层级。"""

    @staticmethod
    def _client(node):
        from hy_memory.client import HyMemoryClient

        class Store:
            async def get_by_id(self, memory_id):
                return node if memory_id == node.node_id else None

        async def no_op_log(**kwargs):
            return None

        client = HyMemoryClient.__new__(HyMemoryClient)
        client._vector_store = Store()
        client._graph_store = None
        client._backend_identity = lambda: {}
        client._maybe_store_op_log = no_op_log
        return client

    @staticmethod
    def _raw_node():
        return MemoryNode(
            node_id="raw-1",
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            content="原始对话",
            layer=MemoryLayer.L1_RAW,
            status=MemoryStatus.ACTIVE,
            custom={"meta": [{"source": "test"}]},
        )

    def test_raw_id_returns_lite_memory(self):
        from hy_memory.client import HyMemoryClient

        client = self._client(self._raw_node())
        raw_get = HyMemoryClient.async_get.__wrapped__
        result = run(raw_get(client, "raw-1"))

        assert result["memory_id"] == "raw-1"
        assert result["content"] == "原始对话"
        assert result["layer"] == MemoryLayer.L1_RAW.value
        assert result["meta"] == [{"source": "test"}]

    def test_public_get_has_no_raw_visibility_switch(self):
        import inspect
        from hy_memory.client import HyMemoryClient

        assert "include_raw" not in inspect.signature(HyMemoryClient.get).parameters
