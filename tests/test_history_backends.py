"""Real SQLite contracts and protocol-level MySQL tests; no emulated SQL engine."""
import asyncio
import inspect
import json
import sys
import tomllib
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

import pytest

from hy_memory.config import CacheConfig, HistoryConfig, MemoryConfig
from hy_memory.data.history_store import HistoryStore, create_history_store
from hy_memory.data.history_store_mysql import MysqlHistoryStore


def config(tmp_path, backend="sqlite"):
    return SimpleNamespace(history=HistoryConfig(backend=backend, db_path=str(tmp_path / "history.db")), cache=CacheConfig())


class FakePool:
    def __init__(self):
        self.calls = []
        self.closed = self.terminated = False
        self.error = None
        self.delay = False
        self.lastrowid = 7
        self.rows = [{"id": 7, "extra": "{}", "old_memory": None}]

    @asynccontextmanager
    async def acquire(self):
        yield self

    @asynccontextmanager
    async def cursor(self, kind):
        yield self

    async def execute(self, sql, params):
        self.calls.append((sql, params))
        assert sql.count("%s") == len(params)
        if self.error:
            raise self.error
        if self.delay:
            await asyncio.Event().wait()

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return {"n": 3}

    def close(self):
        self.closed = True

    def terminate(self):
        self.terminated = True

    async def wait_closed(self):
        pass


@pytest.fixture
def driver(monkeypatch):
    pools, options = [], []

    async def create_pool(**kwargs):
        options.append(kwargs)
        pool = FakePool()
        pools.append(pool)
        return pool

    monkeypatch.setitem(sys.modules, "aiomysql", SimpleNamespace(create_pool=create_pool, DictCursor=object()))
    return pools, options


def test_config_factory(tmp_path, monkeypatch):
    monkeypatch.delenv("MEMORY_HISTORY_BACKEND", raising=False)
    assert HistoryConfig().backend == "sqlite"
    monkeypatch.setenv("MEMORY_HISTORY_BACKEND", " MYSQL ")
    assert HistoryConfig().backend == "mysql"
    monkeypatch.setenv("MEMORY_MYSQL_PASSWORD", "secret")
    cfg = config(tmp_path, "mysql")
    assert isinstance(create_history_store(cfg), MysqlHistoryStore)
    assert type(HistoryStore(cfg)) is HistoryStore
    assert cfg.cache.mysql_password == "secret"
    with pytest.raises(ValueError):
        HistoryConfig(backend="typo")
    cfg.history.backend = "typo"
    with pytest.raises(ValueError):
        create_history_store(cfg)


def test_memory_config_history_backend_round_trip_without_env(monkeypatch):
    monkeypatch.delenv("MEMORY_HISTORY_BACKEND", raising=False)
    cfg = MemoryConfig.from_dict({"history": {"backend": "  MySQL  "}})
    serialized = cfg.to_dict()
    assert serialized["history"]["backend"] == "mysql"
    assert "password" not in serialized["history"]
    assert MemoryConfig.from_dict(serialized).history.backend == "mysql"


@pytest.mark.parametrize("value", [" MYSQL ", "MySqL"])
def test_history_factory_normalizes_from_dict_backend(value):
    cfg = MemoryConfig.from_dict({"history": {"backend": value}})
    assert cfg.history.backend == "mysql"


def test_history_from_dict_rejects_unknown_backend():
    with pytest.raises(ValueError):
        MemoryConfig.from_dict({"history": {"backend": "postgres"}})


def test_mysql_optional_dependency_includes_rsa_auth_support():
    with open("pyproject.toml", "rb") as fh:
        extras = tomllib.load(fh)["project"]["optional-dependencies"]
    assert "aiomysql" in extras["mysql"]
    assert "PyMySQL[rsa]" in extras["mysql"]
    assert "PyMySQL[rsa]" in extras["all"]


@pytest.mark.parametrize("size", [0, -1, 33, True])
def test_pool_bounds(tmp_path, size):
    cfg = config(tmp_path, "mysql")
    cfg.cache.mysql_pool_size = size
    with pytest.raises(ValueError):
        create_history_store(cfg)


@pytest.mark.parametrize("backend", ["sqlite", "mysql"])
async def test_uninitialized(tmp_path, backend):
    store = create_history_store(config(tmp_path, backend))
    assert await store.record_add("m", "x", "raw", "owner") == -1
    assert await store.get_history("m") == []
    assert await store.get_recent("owner") == []
    assert await store.count() == 0
    await store.close()


async def test_sqlite_full_contract(tmp_path, monkeypatch):
    monkeypatch.setattr("hy_memory.data.history_store._now_iso", lambda: "2026-10-17T00:00:00+00:00")
    cfg = config(tmp_path)
    store = create_history_store(cfg)
    await store.initialize()
    ids = [await store.record_add("m'🦊", "中文's", "raw", "Owner", extra={"中文": "🦊"}),
           await store.record_update("m'🦊", "old", "new", "a", "b", "reason", "Owner"),
           await store.record_delete("m'🦊", "new", "Owner")]
    await store.record_search("query's", "Owner", 2)
    for owner in ["owner", "Owner ", "他"]:
        await store.record_add("other", "x", "raw", owner)
    rows = await store.get_history("m'🦊")
    assert [r["id"] for r in rows] == ids
    assert rows[0]["old_memory"] is None
    assert json.loads(rows[0]["extra"]) == {"中文": "🦊"}
    assert rows[1]["old_status"] == "a" and rows[1]["new_status"] == "b"
    assert rows[2]["old_memory"] == "new"
    assert datetime.fromisoformat(rows[0]["created_at"])
    recent = await store.get_recent("Owner", -1)
    assert [r["event"] for r in recent] == ["SEARCH", "DELETE", "UPDATE", "ADD"]
    assert json.loads(recent[0]["extra"])["results_count"] == 2
    assert await store.count("Owner") == 4
    assert await store.count() == 7
    assert await store.get_recent("Owner", 0) == []
    await store.close()
    await store.initialize()
    assert await store.count("Owner") == 4
    await store.close()


async def test_mysql_queries_and_lifecycle(tmp_path, driver):
    pools, options = driver
    cfg = config(tmp_path, "mysql")
    store = create_history_store(cfg)
    await store.initialize()
    pool = pools[0]
    opts = options[0]
    assert opts["db"] == cfg.cache.mysql_database
    assert opts["password"] == cfg.cache.mysql_password
    assert opts["charset"] == "utf8mb4" and opts["maxsize"] == cfg.cache.mysql_pool_size
    assert opts["connect_timeout"] > 0 and opts["autocommit"]
    value = "中文' OR 1=1 -- 🦊"
    assert await store.record_add(value, value, "raw", "Owner ", extra={"x": value}) == 7
    sql, params = pool.calls[-1]
    assert value not in sql and params[0] == value and params[3] is None
    assert json.loads(params[11]) == {"x": value}
    assert params[12] == params[13] and datetime.fromisoformat(params[12])
    await store.record_update(value, "old", "new", "a", "b", "reason", "Owner ")
    assert pool.calls[-1][1][3:8] == ("old", "new", "a", "b", "reason")
    await store.record_delete(value, value, "Owner ")
    await store.record_search(value, "Owner ", 2)
    assert json.loads(pool.calls[-1][1][11])["query"] == value
    assert await store.get_history(value) == pool.rows
    assert "WHERE memory_id = %s AND CAST(memory_id AS BINARY) = CAST(%s AS BINARY)" in pool.calls[-1][0]
    assert pool.calls[-1][1] == (value, value)
    assert "created_at ASC, id ASC" in pool.calls[-1][0]
    await store.get_recent("Owner ", 2)
    assert pool.calls[-1][1] == ("Owner ", "Owner ", 2)
    assert "WHERE isolation_key = %s AND CAST(isolation_key AS BINARY) = CAST(%s AS BINARY)" in pool.calls[-1][0]
    assert "created_at DESC, id DESC" in pool.calls[-1][0]
    await store.get_recent("Owner ", -1)
    assert "LIMIT" not in pool.calls[-1][0]
    assert pool.calls[-1][1] == ("Owner ", "Owner ")
    assert await store.count("Owner ") == 3
    assert pool.calls[-1][1] == ("Owner ", "Owner ")
    assert "WHERE isolation_key = %s AND CAST(isolation_key AS BINARY) = CAST(%s AS BINARY)" in pool.calls[-1][0]
    await store.count()
    assert pool.calls[-1][1] == ()
    await store.initialize()
    assert pool.closed
    await store.close()
    assert pools[-1].closed and store._pool is None
    assert not (tmp_path / "history.db").exists()


async def test_mysql_errors_and_budgets(tmp_path, driver):
    pools, _ = driver
    store = create_history_store(config(tmp_path, "mysql"))
    await store.initialize()
    pool = pools[0]
    pool.error = RuntimeError("query failed")
    for operation in [store.count(), store.get_history("m"), store.record_add("m", "x", "raw", "o")]:
        with pytest.raises(RuntimeError, match="query failed"):
            await operation
    pool.error = None
    pool.delay = True
    store.READ_TIMEOUT = store.WRITE_TIMEOUT = .01
    for operation in [store.count(), store.record_delete("m", "x", "o")]:
        with pytest.raises(TimeoutError):
            await operation
    pool.delay = False
    calls = len(pool.calls)
    assert await store.count() == 3
    assert len(pool.calls) == calls + 1
    await store.close()
    assert pool.closed


async def test_failed_initialize_cleanup(tmp_path, driver, monkeypatch):
    pools, _ = driver
    store = create_history_store(config(tmp_path, "mysql"))
    async def fail(*args):
        raise RuntimeError("DDL failed")
    monkeypatch.setattr(store, "_execute", fail)
    with pytest.raises(RuntimeError, match="DDL failed"):
        await store.initialize()
    assert pools[0].terminated and store._pool is None
    assert not (tmp_path / "history.db").exists()


def test_client_uses_factory():
    from hy_memory.client import HyMemoryClient
    source = inspect.getsource(HyMemoryClient)
    assert "create_history_store(self._config)" in source
    assert "HistoryStore(self._config)" not in source


@pytest.mark.parametrize("stage", ["connect", "init", "close"])
async def test_lifecycle_timeouts(tmp_path, driver, monkeypatch, stage):
    pools, _ = driver
    store = create_history_store(config(tmp_path, "mysql"))
    if stage == "connect":
        async def hang(**kwargs):
            await asyncio.Event().wait()
        monkeypatch.setattr(sys.modules["aiomysql"], "create_pool", hang)
        store.CONNECT_TIMEOUT = .01
        with pytest.raises(TimeoutError):
            await store.initialize()
        assert store._pool is None
    elif stage == "init":
        async def hang(*args):
            await asyncio.Event().wait()
        monkeypatch.setattr(store, "_execute", hang)
        store.INIT_TIMEOUT = .01
        with pytest.raises(TimeoutError):
            await store.initialize()
        assert pools[0].terminated and store._pool is None
    else:
        await store.initialize()
        async def hang():
            await asyncio.Event().wait()
        monkeypatch.setattr(pools[0], "wait_closed", hang)
        store.CLOSE_TIMEOUT = .01
        with pytest.raises(TimeoutError):
            await store.close()
        assert pools[0].terminated and store._pool is None


async def test_missing_driver_no_fallback(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "aiomysql", None)
    store = create_history_store(config(tmp_path, "mysql"))
    with pytest.raises(ModuleNotFoundError):
        await store.initialize()
    assert store._pool is None
    assert not (tmp_path / "history.db").exists()


async def test_connect_failure_then_reinitialize(tmp_path, driver, monkeypatch):
    pools, _ = driver
    fake_driver = sys.modules["aiomysql"]
    original_create = fake_driver.create_pool
    store = create_history_store(config(tmp_path, "mysql"))
    attempts = 0

    async def fail(**kwargs):
        nonlocal attempts
        attempts += 1
        raise RuntimeError("connection rejected")

    monkeypatch.setattr(fake_driver, "create_pool", fail)
    with pytest.raises(RuntimeError, match="connection rejected"):
        await store.initialize()
    assert attempts == 1 and store._pool is None and not pools
    monkeypatch.setattr(fake_driver, "create_pool", original_create)
    await store.initialize()
    assert await store.count() == 3
    await store.close()
    assert not (tmp_path / "history.db").exists()
