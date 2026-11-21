"""Local Qdrant must keep its SQLite connection on one owner thread."""

import asyncio
import threading
import time
from typing import Optional

import pytest

qdrant_client = pytest.importorskip("qdrant_client")

from qdrant_client import QdrantClient, models  # noqa: E402
from qdrant_client.local.persistence import CollectionPersistence  # noqa: E402

from hy_memory.config import MemoryConfig, VectorStoreConfig  # noqa: E402
from hy_memory.data.vector_store import create_vector_store  # noqa: E402
from hy_memory.data.vector_store_qdrant import _LocalQdrantOwner  # noqa: E402
from hy_memory.models.memory import MemoryLayer, MemoryNode  # noqa: E402


def _config(directory, name="thread_probe"):
    config = MemoryConfig()
    config.vector_store = VectorStoreConfig(
        provider="qdrant",
        persist_directory=str(directory),
        collection_name=name,
        embedding_dims=4,
    )
    return config


def _node(index=0):
    return MemoryNode(
        node_id=f"test-node-{index}",
        user_id="test-user",
        agent_id="test-agent",
        session_id="test-session",
        layer=MemoryLayer.L2_FACT,
        content=f"Offline owner-thread test {index}",
        embedding=[1.0, 0.0, 0.0, float(index)],
    )


@pytest.fixture
def same_thread_qdrant(monkeypatch):
    monkeypatch.setattr(CollectionPersistence, "CHECK_SAME_THREAD", True)
    monkeypatch.setattr(
        "hy_memory.pipelines._retrieval.bm25_fastembed.is_available",
        lambda: False,
    )


@pytest.mark.asyncio
async def test_reopened_local_collection_writes_with_same_thread_check(
    tmp_path,
    same_thread_qdrant,
):
    client = QdrantClient(path=str(tmp_path))
    client.create_collection(
        "thread_probe_4",
        vectors_config=models.VectorParams(
            size=4,
            distance=models.Distance.COSINE,
        ),
    )
    client.close()

    store = create_vector_store(_config(tmp_path))
    await store.initialize()
    try:
        assert await store.upsert(_node()) == "test-node-0"
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_fresh_local_collection_serializes_concurrent_writes_and_close(
    tmp_path,
    same_thread_qdrant,
):
    store = create_vector_store(_config(tmp_path))
    await store.initialize()
    try:
        ids = await asyncio.gather(*(store.upsert(_node(index)) for index in range(8)))
        assert ids == [f"test-node-{index}" for index in range(8)]
    finally:
        await store.close()


class _SlowCloseClient:
    """QdrantClient stand-in whose close() takes a measurable amount of time."""

    def __init__(self, close_seconds: float) -> None:
        self._close_seconds = close_seconds
        self.closed = False
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        time.sleep(self._close_seconds)
        self.closed = True


def test_local_owner_close_is_a_barrier_for_concurrent_callers():
    # Regression for #454: a close() that arrives while another close is still
    # running must wait for it instead of returning while the client (and its
    # SQLite storage lock) is still open.
    client = _SlowCloseClient(close_seconds=0.3)
    owner = _LocalQdrantOwner(lambda: client)

    second_elapsed: dict = {}

    def second_close() -> None:
        time.sleep(0.05)  # let the first close enter its in-flight window
        started = time.monotonic()
        owner.close()
        second_elapsed["duration"] = time.monotonic() - started

    thread = threading.Thread(target=second_close)
    thread.start()
    owner.close()  # first close: blocks for the full close duration
    thread.join()

    assert client.closed, "owner close did not actually close the client"
    assert second_elapsed["duration"] >= 0.15, (
        "second close returned while the first close was still running "
        f"({second_elapsed['duration']:.3f}s)"
    )


def test_local_owner_close_is_safe_after_failed_factory():
    # 工厂构造失败：__init__ 已置 _closed 并关闭 executor，close() 必须静默返回
    # 而不是向已关闭的 executor 再提交任务。
    def broken_factory():
        raise RuntimeError("qdrant storage is not writable")

    with pytest.raises(RuntimeError, match="not writable"):
        _LocalQdrantOwner(broken_factory)


@pytest.mark.asyncio
async def test_store_close_waits_for_in_flight_local_client_release(
    tmp_path,
    monkeypatch,
):
    # Regression for #454: the second of two concurrent store.close() calls used
    # to see _client=None and return before the first close released the local
    # storage lock, so an immediate reopen failed with
    # "Storage folder ... is already accessed by another instance".
    store = create_vector_store(_config(tmp_path))
    await store.initialize()

    real_close = QdrantClient.close

    def slow_close(self, *args, **kwargs):
        time.sleep(0.3)
        return real_close(self, *args, **kwargs)

    monkeypatch.setattr(QdrantClient, "close", slow_close)
    first: Optional[asyncio.Task] = None
    try:
        first = asyncio.create_task(store.close())
        await asyncio.sleep(0.05)

        started = time.monotonic()
        await store.close()
        elapsed = time.monotonic() - started
        assert elapsed >= 0.15, (
            f"second close returned before the first close finished ({elapsed:.3f}s)"
        )

        await first
    finally:
        monkeypatch.undo()
        if first is not None:
            try:
                await first
            except Exception:  # noqa: BLE001 - 清理阶段不掩盖断言失败
                pass
        await store.close()

    # Once any close() has returned, the storage directory must be openable.
    reopened = QdrantClient(path=str(tmp_path))
    reopened.close()
