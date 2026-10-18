"""Opt-in tests for an operator-provisioned throwaway MySQL database.

Only HY_TEST_MYSQL_* connection variables are read. No deletes or database DDL.
Rows use a unique namespace and intentionally remain available for inspection.
"""
import asyncio
import json
import os
import re
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from hy_memory.config import CacheConfig, HistoryConfig
from hy_memory.data.history_store import create_history_store

pytestmark = pytest.mark.integration


@pytest.fixture
def mysql_config(tmp_path):
    names = ("HOST", "PORT", "USER", "PASSWORD", "DATABASE")
    values = {name: os.environ.get("HY_TEST_MYSQL_" + name) for name in names}
    if any(value is None for value in values.values()):
        pytest.skip("Explicit HY_TEST_MYSQL_{HOST,PORT,USER,PASSWORD,DATABASE} required")
    if not re.fullmatch(r"hy_canary_[A-Za-z0-9_]+", values["DATABASE"]):
        pytest.fail("Integration database must match hy_canary_[A-Za-z0-9_]+")
    pytest.importorskip("aiomysql")
    return SimpleNamespace(
        history=HistoryConfig(backend="mysql", db_path=str(tmp_path / "history.db")),
        cache=CacheConfig(
            backend="sqlite", mysql_host=values["HOST"], mysql_port=int(values["PORT"]),
            mysql_user=values["USER"], mysql_password=values["PASSWORD"],
            mysql_database=values["DATABASE"], mysql_pool_size=2, mysql_pool_recycle=60,
        ),
    )


async def test_mysql_history_persistence(mysql_config, tmp_path, monkeypatch):
    namespace = "hy_history_" + uuid4().hex
    owner = namespace + ":Owner中文🦊"
    memory = namespace + ":memory's🦊"
    # Tie ordering is observable, independently of server clock resolution.
    monkeypatch.setattr("hy_memory.data.history_store_mysql._now_iso", lambda: "2026-10-17T00:00:00+00:00")
    store = create_history_store(mysql_config)
    try:
        await store.initialize()
        ids = [await store.record_add(memory, "中文's🦊", "raw", owner, actor_id="actor's", extra={"键": "值🦊"}),
               await store.record_update(memory, "中文's🦊", "new's", "old", "new", "why's", owner),
               await store.record_delete(memory, "new's", owner)]
        search_id = await store.record_search("query's中文", owner, 2, extra={"a": None})
        for key in (owner.lower(), owner + " ", namespace + ":他"):
            await store.record_add(namespace + ":other", "other", "raw", key)
        # Existing get_history contract crosses owners for a shared memory ID.
        cross_id = await store.record_add(memory, "cross", "raw", namespace + ":cross")
        for variant in (memory.upper(), memory + " "):
            await store.record_add(variant, "variant", "raw", namespace + ":variants")
            assert len(await store.get_history(variant)) == 1
        rows = await store.get_history(memory)
        assert [r["id"] for r in rows] == ids + [cross_id]
        assert all(isinstance(i, int) for i in ids)
        assert set(rows[0]) == {"id", "memory_id", "isolation_key", "event", "old_memory", "new_memory", "old_status", "new_status", "change_reason", "layer", "actor_id", "role", "extra", "created_at", "updated_at"}
        assert rows[0]["new_memory"] == "中文's🦊" and rows[0]["old_memory"] is None
        assert rows[0]["layer"] == "raw" and rows[0]["actor_id"] == "actor's" and rows[0]["role"] == ""
        assert json.loads(rows[0]["extra"]) == {"键": "值🦊"}
        assert isinstance(rows[0]["extra"], str)
        assert rows[1]["old_memory"] == "中文's🦊" and rows[1]["new_memory"] == "new's"
        assert rows[1]["old_status"] == "old" and rows[1]["new_status"] == "new"
        assert rows[1]["change_reason"] == "why's" and rows[1]["layer"] is None
        assert rows[2]["old_memory"] == "new's" and rows[2]["new_memory"] is None
        assert datetime.fromisoformat(rows[0]["created_at"]).tzinfo
        assert rows[0]["created_at"] == rows[0]["updated_at"]
        recent = await store.get_recent(owner, -1)
        assert [r["id"] for r in recent] == [search_id] + ids[::-1]
        assert json.loads(recent[0]["extra"]) == {"query": "query's中文", "results_count": 2, "a": None}
        assert await store.count(owner) == 4
        for key in (owner.lower(), owner + " ", namespace + ":他"):
            assert await store.count(key) == 1
            assert len(await store.get_recent(key)) == 1
        assert len(await store.get_recent(owner, 2)) == 2
        assert await store.get_recent(owner, 0) == []
        await store.close()
        await store.initialize()
        assert await store.count(owner) == 4
        assert [r["id"] for r in await store.get_history(memory)] == ids + [cross_id]
        # Real server syntax failure must propagate, never become empty/success.
        with pytest.raises(Exception):
            await store._execute("SELECT * FROM", (), "rows")
        assert not (tmp_path / "history.db").exists()
    finally:
        await store.close()


async def test_mysql_timeout_recovery(mysql_config, tmp_path, monkeypatch):
    import aiomysql

    mysql_config.cache.mysql_pool_size = 1
    store = create_history_store(mysql_config)
    attempts = 0
    original_execute = aiomysql.cursors.Cursor.execute

    async def tracked_execute(cursor, query, args=None):
        nonlocal attempts
        if query == "SELECT SLEEP(1) AS n":
            attempts += 1
        return await original_execute(cursor, query, args)

    monkeypatch.setattr(aiomysql.cursors.Cursor, "execute", tracked_execute)
    try:
        await store.initialize()
        with monkeypatch.context() as budget:
            budget.setattr(store, "READ_TIMEOUT", 0.05)
            with pytest.raises(asyncio.TimeoutError):
                await store._execute("SELECT SLEEP(1) AS n", (), "count")
        assert attempts == 1
        assert await asyncio.wait_for(store._execute("SELECT 1 AS n", (), "count"), 3) == 1
        assert isinstance(await asyncio.wait_for(store.count(), 3), int)
        assert attempts == 1
        assert not (tmp_path / "history.db").exists()
    finally:
        await store.close()


async def test_mysql_initialization_failure_recovery(mysql_config, tmp_path):
    import aiomysql

    store = create_history_store(mysql_config)
    correct_password = mysql_config.cache.mysql_password
    try:
        mysql_config.cache.mysql_password = uuid4().hex + uuid4().hex
        # Do not include credentials or server exception text in assertions.
        with pytest.raises(aiomysql.OperationalError):
            await store.initialize()
        assert store._pool is None
        mysql_config.cache.mysql_password = correct_password
        await store.initialize()
        assert await store._execute("SELECT 1 AS n", (), "count") == 1
        assert not (tmp_path / "history.db").exists()
    finally:
        mysql_config.cache.mysql_password = correct_password
        await store.close()
