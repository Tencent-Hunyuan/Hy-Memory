# -*- coding: utf-8 -*-
"""History queries without agent_ids must cover every agent of the user."""

import asyncio

from hy_memory.config import MemoryConfig
from hy_memory.data.history_store import HistoryStore


def _store(tmp_path):
    config = MemoryConfig()
    config.history.db_path = str(tmp_path / "history.db")
    return HistoryStore(config)


def test_get_recent_for_user_matches_all_agents(tmp_path):
    async def _run():
        store = _store(tmp_path)
        await store.initialize()
        try:
            keys = [
                "u1:default_agent",
                "u1:agent_b",
                "u10:default_agent",
                "u2:default_agent",
            ]
            for index, key in enumerate(keys):
                await store.record_add(
                    memory_id=f"m{index}",
                    content="x",
                    layer="l1_raw",
                    isolation_key=key,
                )
            rows = await store.get_recent_for_user("u1", limit=10)
        finally:
            await store.close()
        return [row["isolation_key"] for row in rows]

    assert set(asyncio.run(_run())) == {"u1:default_agent", "u1:agent_b"}


def test_get_recent_for_user_escapes_like_wildcards(tmp_path):
    async def _run():
        store = _store(tmp_path)
        await store.initialize()
        try:
            await store.record_add(
                memory_id="m1", content="x", layer="l1_raw",
                isolation_key="u_1:default_agent",
            )
            await store.record_add(
                memory_id="m2", content="x", layer="l1_raw",
                isolation_key="uX1:default_agent",
            )
            rows = await store.get_recent_for_user("u_1", limit=10)
        finally:
            await store.close()
        return [row["isolation_key"] for row in rows]

    assert asyncio.run(_run()) == ["u_1:default_agent"]
