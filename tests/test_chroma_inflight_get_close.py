"""Regression tests for Chroma get_by_id / close overlap.

Uses a fake Chroma collection and the SDK's real worker executor. No database,
embedding API, LLM, or network connection is needed. Intended to live in tests/.
"""

import asyncio
import threading
import uuid

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.data import vector_store_chroma as chroma_module


def _make_store_with_collection():
    config = MemoryConfig.from_dict({
        "vector_store": {
            "provider": "chroma",
            "collection_name": "chroma_inflight_close_test",
            "embedding_dims": 8,
        },
        "history": {"enable": False},
    })
    store = chroma_module.ChromaVectorStore(config)
    node_id = str(uuid.uuid4())  # Chroma point_id == UUID node_id
    vector = [1.0] + [0.0] * 7

    class Collection:
        def get(self, *, ids, include):
            assert ids == [node_id]
            assert set(include) == {"metadatas", "embeddings", "documents"}
            return {
                "ids": [node_id],
                "metadatas": [{
                    "node_id": node_id,
                    "user_id": "synthetic_user",
                    "agent_id": "synthetic_agent",
                    "layer": "l2_fact",
                    "status": "active",
                    "content": "A fictitious user likes badminton.",
                }],
                "embeddings": [vector],
                "documents": ["A fictitious user likes badminton."],
            }

    store._collection = Collection()
    return store, node_id, vector


@pytest.mark.asyncio
async def test_get_by_id_worker_accepted_before_close_returns_existing_node(
    monkeypatch, caplog,
):
    """An already-scheduled read must not turn into a false ID miss on close."""
    store, node_id, vector = _make_store_with_collection()
    worker_entered = threading.Event()
    worker_release = threading.Event()
    original_executor = chroma_module._run_in_vdb_pool

    async def paused_executor(callback, *args, **kwargs):
        def paused_worker():
            worker_entered.set()
            if not worker_release.wait(timeout=8):
                raise TimeoutError("test worker barrier was not released")
            return callback(*args, **kwargs)

        return await original_executor(paused_worker)

    monkeypatch.setattr(chroma_module, "_run_in_vdb_pool", paused_executor)
    task = asyncio.create_task(store.get_by_id(node_id))
    try:
        assert await asyncio.to_thread(worker_entered.wait, 5), (
            "the Chroma get_by_id worker did not enter its test barrier"
        )
        await store.close()
        worker_release.set()
        loaded = await asyncio.wait_for(task, timeout=5)

        assert loaded is not None, "an accepted read became a false miss during close"
        assert loaded.node_id == node_id
        assert loaded.embedding == vector
        assert not any(
            "Failed to get vector point" in record.getMessage()
            for record in caplog.records
        )
    finally:
        worker_release.set()
        if not task.done():
            await asyncio.wait_for(task, timeout=9)
        await store.close()


@pytest.mark.asyncio
async def test_get_by_id_normal_read_and_missing_id(caplog):
    store, node_id, vector = _make_store_with_collection()
    existing_collection = store._collection

    class ExistingOrMissingCollection:
        def get(self, *, ids, include):
            if ids == [node_id]:
                return existing_collection.get(ids=ids, include=include)
            return {"ids": [], "metadatas": [], "embeddings": None, "documents": None}

    store._collection = ExistingOrMissingCollection()
    try:
        found = await store.get_by_id(node_id)
        assert found is not None and found.embedding == vector
        absent = await store.get_by_id(str(uuid.uuid4()))
        assert absent is None
        assert not any(
            "Failed to get vector point" in record.getMessage()
            for record in caplog.records
        )
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_chroma_close_twice_is_idempotent():
    store, _, _ = _make_store_with_collection()
    await store.close()
    await store.close()
    assert store._collection is None
