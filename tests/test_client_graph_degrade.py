# -*- coding: utf-8 -*-
"""
ultra 模式下 GraphStore 不可用时，client 必须降级运行而不是构造期抛错。

Kuzu 在同一数据目录的第二个进程上拿不到文件锁，会静默降级为 no-op；此前 client
把这当成致命错误抛 RuntimeError，导致同一目录上的常驻服务、后台写 worker 和 CLI
短命令全部起不来（issue #276）。
"""

import uuid

import pytest

from hy_memory import HyMemoryClient, MemoryConfig


class _StubGraphStore:
    """最小 GraphStore 替身：只暴露 client 关心的可用性标记。"""

    def __init__(self, available: bool) -> None:
        self._available = available
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        self.closed = True


def _config(tmp_path):
    suffix = uuid.uuid4().hex[:8]
    return MemoryConfig.from_dict(
        {
            "vector_store": {
                "provider": "chroma",
                "collection_name": f"test_graph_degrade_{suffix}",
                "persist_directory": str(tmp_path / "chroma"),
                "embedding_dims": 8,
            },
            "embedder": {"embedding_dims": 8},
            "cache": {"backend": "sqlite", "sqlite_path": str(tmp_path / "cache.db")},
            "history": {"enable": False},
            "graph_store": {"provider": "kuzu", "db_path": str(tmp_path / "kuzu_db")},
        }
    )


@pytest.fixture
def stub_graph_store(monkeypatch):
    """把 create_graph_store 换成可控替身，避免真的去开 Kuzu。"""

    state = {"store": None}

    def _install(available: bool):
        from hy_memory.data import graph_store as graph_store_module

        store = _StubGraphStore(available)
        state["store"] = store
        monkeypatch.setattr(
            graph_store_module, "create_graph_store", lambda _config: store
        )
        return store

    return _install


def test_ultra_degrades_instead_of_raising_when_graph_unavailable(
    tmp_path, stub_graph_store
):
    store = stub_graph_store(available=False)

    client = HyMemoryClient(config=_config(tmp_path), mode="ultra")
    try:
        assert store.initialized is True
        assert client.graph_degraded is True
        assert "unavailable" in (client.graph_degraded_reason or "")
        # 降级不等于放弃：图存储对象仍在，只是不可用
        assert client._graph_store is store
    finally:
        client.close()


def test_ultra_reports_no_degradation_when_graph_is_available(tmp_path, stub_graph_store):
    stub_graph_store(available=True)

    client = HyMemoryClient(config=_config(tmp_path), mode="ultra")
    try:
        assert client.graph_degraded is False
        assert client.graph_degraded_reason is None
    finally:
        client.close()
