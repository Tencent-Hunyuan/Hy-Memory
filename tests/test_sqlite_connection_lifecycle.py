import asyncio
import threading

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.data.cache_sqlite import SqliteCache
from hy_memory.data.history_store import HistoryStore


class _BlockingCursor:
    def fetchone(self):
        return (1,)

    def fetchall(self):
        return []


class _BlockingConnection:
    """Hold execute() open so a concurrent close() can be observed."""

    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.closed = threading.Event()

    def execute(self, *_args, **_kwargs):
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test did not release the blocking query")
        if self.closed.is_set():
            raise AssertionError("connection was closed while execute() was active")
        return _BlockingCursor()

    def close(self):
        self.closed.set()


def _config(tmp_path) -> MemoryConfig:
    config = MemoryConfig()
    config.history.db_path = str(tmp_path / "history.db")
    config.cache.db_path = str(tmp_path / "cache.db")
    return config


async def _wait_for(event: threading.Event) -> None:
    assert await asyncio.wait_for(asyncio.to_thread(event.wait, 1), timeout=2)


@pytest.mark.asyncio
async def test_history_close_waits_for_active_query(tmp_path):
    store = HistoryStore(_config(tmp_path))
    connection = _BlockingConnection()
    store._conn = connection

    query = asyncio.create_task(store.count())
    await _wait_for(connection.entered)
    closing = asyncio.create_task(store.close())
    await asyncio.sleep(0.05)

    assert not connection.closed.is_set()
    connection.release.set()
    assert await query == 1
    await closing
    assert connection.closed.is_set()


@pytest.mark.asyncio
async def test_cache_close_waits_for_active_query(tmp_path):
    store = SqliteCache(_config(tmp_path))
    connection = _BlockingConnection()
    store._conn = connection

    query = asyncio.create_task(store.load_metrics_range("0", "9"))
    await _wait_for(connection.entered)
    closing = asyncio.create_task(store.close())
    await asyncio.sleep(0.05)

    assert not connection.closed.is_set()
    connection.release.set()
    assert await query == []
    await closing
    assert connection.closed.is_set()


@pytest.mark.asyncio
async def test_history_concurrent_reads_and_writes_are_serialized(tmp_path):
    store = HistoryStore(_config(tmp_path))
    await store.initialize()
    try:
        inserted = await asyncio.gather(
            *(
                store.record_add(
                    memory_id=f"memory-{index}",
                    content="content",
                    layer="l2_fact",
                    isolation_key="user",
                )
                for index in range(200)
            )
        )
        assert all(row_id > 0 for row_id in inserted)
        assert len(set(inserted)) == 200

        counts = await asyncio.gather(*(store.count() for _ in range(100)))
        assert counts == [200] * 100
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_cache_concurrent_pipeline_log_reads_and_writes_are_serialized(tmp_path):
    store = SqliteCache(_config(tmp_path))
    await store.initialize()
    try:
        written = await asyncio.gather(
            *(
                store.store_pipeline_log(
                    request_id="request",
                    user_id="user",
                    agent_id="agent",
                    step="EXTRACT",
                    prompt=f"prompt-{index}",
                    response="response",
                )
                for index in range(200)
            )
        )
        assert written == [True] * 200

        pages = await asyncio.gather(
            *(store.get_pipeline_logs(request_id="request", limit=200) for _ in range(50))
        )
        assert all(len(page) == 200 for page in pages)
    finally:
        await store.close()
