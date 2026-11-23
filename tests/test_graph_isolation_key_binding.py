"""Kuzu 的 expand_with_tags 必须把 isolation_key 当参数绑定，而不是拼进 Cypher。

两个后果都是实测过的：

- 隔离键里带撇号（user_id 形如 `o'brien`）时，拼出来的语句是语法错误，异常被
  `except` 吞掉，调用方拿到空列表 —— 看起来像"这个租户没有可展开的邻居"。
- 传入 `alice::ag1::s1' OR '1'='1` 会把 WHERE 变成恒真，把别的租户的节点一起
  展开出来（跨租户泄漏）。
"""

import asyncio

import pytest

pytest.importorskip("kuzu")

from hy_memory.config import GraphStoreConfig, MemoryConfig
from hy_memory.data.graph_store_kuzu import KuzuGraphStore
from hy_memory.models.memory import MemoryLayer, MemoryNode


def _node(user_id: str, content: str) -> MemoryNode:
    return MemoryNode(
        node_id=f"{user_id}:{content}", user_id=user_id, agent_id="ag1",
        session_id="s1", layer=MemoryLayer.L2_FACT, content=content,
    )


async def _store(tmp_path):
    cfg = MemoryConfig()
    cfg.graph_store = GraphStoreConfig(provider="kuzu", db_path=str(tmp_path / "g"))
    store = KuzuGraphStore(cfg)
    await store.initialize()
    if not getattr(store, "_available", False):
        pytest.skip("Kuzu unavailable in this environment")
    return store


async def _seed(store):
    """两个租户各一对同租户邻居，外加一条跨租户脏边（历史数据里会存在）。"""
    alice = _node("alice", "note")
    alice_sibling = _node("alice", "sibling")
    bob = _node("bob", "secret")
    for node in (alice, alice_sibling, bob):
        await store.upsert_memory_node(node)
    assert await store.add_edge(alice.node_id, alice_sibling.node_id, "RELATED_TO")
    assert await store.add_edge(alice.node_id, bob.node_id, "RELATED_TO")
    return alice, alice_sibling, bob


def test_expands_within_the_requested_isolation_key(tmp_path):
    async def run():
        store = await _store(tmp_path)
        try:
            alice, alice_sibling, _ = await _seed(store)
            out = await store.expand_with_tags(
                [alice.node_id], hop=1, isolation_key=alice.get_isolation_key(),
            )
            assert [item["node_id"] for item in out] == [alice_sibling.node_id]
        finally:
            await store.close()

    asyncio.run(run())


def test_isolation_key_cannot_be_used_to_reach_another_tenant(tmp_path):
    """`x' OR '1'='1` 曾经让 WHERE 恒真，把别的租户的节点带出来。"""

    async def run():
        store = await _store(tmp_path)
        try:
            alice, alice_sibling, bob = await _seed(store)
            out = await store.expand_with_tags(
                [alice.node_id], hop=1,
                isolation_key=f"{alice.get_isolation_key()}' OR '1'='1",
            )
            returned = {item["node_id"] for item in out}
            assert bob.node_id not in returned
            # 绑定成参数后，这个串只是一个不存在的隔离键 —— 什么都不该展开。
            assert returned == set()
        finally:
            await store.close()

    asyncio.run(run())


def test_isolation_key_with_an_apostrophe_still_expands(tmp_path):
    """user_id 里带撇号是合法输入，不能因为拼字符串变成语法错误而静默返回空。"""

    async def run():
        store = await _store(tmp_path)
        try:
            odd = _node("o'brien", "note")
            odd_sibling = _node("o'brien", "sibling")
            for node in (odd, odd_sibling):
                await store.upsert_memory_node(node)
            assert await store.add_edge(odd.node_id, odd_sibling.node_id, "RELATED_TO")

            out = await store.expand_with_tags(
                [odd.node_id], hop=1, isolation_key=odd.get_isolation_key(),
            )
            assert [item["node_id"] for item in out] == [odd_sibling.node_id]
        finally:
            await store.close()

    asyncio.run(run())
