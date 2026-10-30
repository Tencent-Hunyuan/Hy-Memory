# -*- coding: utf-8 -*-
"""list_memories(scene=...) 与 memory_stats：三类记忆都列得出、数得清。

MemHub 的记忆库只调 list_memories，不传 workspace_id 时只列 chat，coding /
working 两类经验既列不出也数不到：一个 45 条记忆的库在界面上只剩 22 条。
"""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.coding.store import CodingMemoryStore
from hy_memory.coding.types import CodingMemory, WorkingFact
from hy_memory.models.memory import MemoryLayer, MemoryStatus

BASE = datetime(2026, 10, 20, 8, 0, 0)


def _node(node_id, *, created, modified=None, layer=MemoryLayer.L2_FACT):
    return SimpleNamespace(
        node_id=node_id,
        layer=layer,
        gmt_created=created,
        gmt_modified=modified,
        valid_from=None,
        session_id="s",
        source="cursor",
    )


class _FakeVectorStore:
    def __init__(self, nodes):
        self.nodes = nodes

    async def list_by_user(self, *, layers=None, **_):
        if layers == [MemoryLayer.L0_BASIC_INFO]:
            return []
        return list(self.nodes)


async def _store(coding_config, mock_embed, variant):
    store = CodingMemoryStore(coding_config, embed_service=mock_embed, variant=variant)
    await store.initialize()
    return store


def _client(nodes, stores):
    client = object.__new__(HyMemoryClient)
    client._vector_store = _FakeVectorStore(nodes)
    client._coding_stores = dict(stores)
    client._config = None
    return client


def _unwrapped(name):
    # The public async_* methods hop onto the client's own loop thread; the
    # logic under test does not need one.
    return getattr(HyMemoryClient, name).__wrapped__


async def test_stats_counts_every_scene_with_its_latest_times(coding_config, mock_embed):
    coding = await _store(coding_config, mock_embed, "coding")
    working = await _store(coding_config, mock_embed, "working")
    try:
        await coding.insert(CodingMemory(
            experience_id="c1", user_id="u", task="T", solution="S",
            created_at=BASE, updated_at=BASE + timedelta(hours=1),
        ))
        await coding.insert(CodingMemory(
            experience_id="c2", user_id="u", task="T", solution="S",
            workspace_id="ws/a",
            created_at=BASE + timedelta(hours=2), updated_at=BASE + timedelta(hours=3),
        ))
        await coding.insert(CodingMemory(
            experience_id="other-user", user_id="v", task="T", solution="S",
        ))
        await working.insert(CodingMemory(
            experience_id="w1", user_id="u", task="T", solution="S",
            created_at=BASE, updated_at=BASE,
        ))
        await working.insert_fact(WorkingFact(fact_id="f1", user_id="u", statement="x"))
        nodes = [
            _node("m1", created=BASE, modified=BASE + timedelta(days=1)),
            _node("m2", created=BASE + timedelta(hours=5)),
        ]
        client = _client(nodes, {"coding": coding, "working": working})

        stats = await _unwrapped("async_memory_stats")(client, user_id="u")

        assert stats["total"] == 2 + 2 + 1
        assert stats["scenes"]["chat"] == {
            "count": 2,
            "latest_created_at": int((BASE + timedelta(hours=5)).timestamp()),
            "latest_updated_at": int((BASE + timedelta(days=1)).timestamp()),
        }
        assert stats["scenes"]["coding"]["count"] == 2
        # Productivity stores write naive UTC (utcnow), so naive means UTC.
        assert stats["scenes"]["coding"]["latest_created_at"] == int(
            (BASE + timedelta(hours=2)).replace(tzinfo=timezone.utc).timestamp()
        )
        # The store stamps updated_at on write; the stat is the newest it holds.
        stored = [await coding.get_by_id(eid, user_id="u") for eid in ("c1", "c2")]
        assert stats["scenes"]["coding"]["latest_updated_at"] == max(
            int(item.updated_at.replace(tzinfo=timezone.utc).timestamp()) for item in stored
        )
        assert stats["scenes"]["working"]["count"] == 1
        assert stats["scenes"]["working"]["facts"] == 1
    finally:
        await coding.close()
        await working.close()


async def test_stats_of_an_unwritten_scene_is_zero_without_creating_its_database(
    coding_config,
):
    client = _client([], {})
    client._config = coding_config

    stats = await _unwrapped("async_memory_stats")(client, user_id="u")

    assert stats["total"] == 0
    assert stats["scenes"]["coding"] == {
        "count": 0, "latest_created_at": None, "latest_updated_at": None, "facts": 0,
    }
    assert not CodingMemoryStore.database_exists(
        coding_config, db_path=coding_config.coding.db_path, variant="coding"
    )


async def test_list_by_scene_reads_that_scene_alone_with_its_real_total(
    coding_config, mock_embed,
):
    coding = await _store(coding_config, mock_embed, "coding")
    working = await _store(coding_config, mock_embed, "working")
    try:
        for index in range(3):
            await coding.insert(CodingMemory(
                experience_id=f"c{index}", user_id="u", task=f"T{index}", solution="S",
                workspace_id="ws/a" if index == 0 else None,
                updated_at=BASE + timedelta(hours=index),
            ))
        await working.insert(CodingMemory(
            experience_id="w1", user_id="u", task="W", solution="S",
        ))
        client = _client([], {"coding": coding, "working": working})
        list_memories = _unwrapped("async_list_memories")

        page = await list_memories(client, user_id="u", scene="coding", limit=2)
        assert page["variant"] == "coding"
        assert [item["experience_id"] for item in page["experiences"]] == ["c2", "c1"]
        assert page["total"] == 3

        scoped = await list_memories(client, user_id="u", scene="coding", workspace_id="ws/a")
        assert [item["experience_id"] for item in scoped["experiences"]] == ["c0"]
        assert scoped["total"] == 1

        other = await list_memories(client, user_id="u", scene="working")
        assert [item["experience_id"] for item in other["experiences"]] == ["w1"]

        archived = await list_memories(
            client, user_id="u", scene="coding", statuses=[MemoryStatus.ARCHIVED]
        )
        assert archived["experiences"] == [] and archived["total"] == 0
    finally:
        await coding.close()
        await working.close()


def test_an_unknown_scene_is_refused():
    with pytest.raises(ValueError, match="scene"):
        HyMemoryClient._list_scene("raw")
    assert HyMemoryClient._list_scene(None) is None
    assert HyMemoryClient._list_scene(" Coding ") == "coding"


def test_list_without_scene_still_lists_chat(monkeypatch):
    client = _client([_node("m1", created=BASE)], {})
    client._sort_memory_nodes = lambda nodes, order: nodes
    client._memory_node_to_list_item = lambda node: {"memory_id": node.node_id}

    async def no_graph(**_):
        return None

    async def no_op_log(**_):
        return None

    client._list_graph_bucket = no_graph
    client._maybe_store_op_log = no_op_log
    client._backend_identity = lambda: {}
    client._normalize_layers = lambda layers: layers

    out = asyncio.run(_unwrapped("async_list_memories")(client, user_id="u"))

    assert out["vdb"]["total"] == 1
    assert out["vdb"]["memories"] == [{"memory_id": "m1"}]
