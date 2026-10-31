"""A rejected vector write must not change the searchable collection."""

import asyncio
from copy import deepcopy

import pytest

pytest.importorskip("faiss")

from hy_memory.config import MemoryConfig
from hy_memory.data.vector_store_faiss import FaissVectorStore
from hy_memory.models.memory import MemoryLayer, MemoryNode


def _config(tmp_path):
    return MemoryConfig.from_dict({
        "vector_store": {
            "provider": "faiss",
            "collection_name": "rejected_writes",
            "persist_directory": str(tmp_path),
            "embedding_dims": 4,
        },
    })


def _node(content="original", embedding=None):
    return MemoryNode(
        user_id="test-user", agent_id="test-agent", session_id="test-session",
        layer=MemoryLayer.L2_FACT, content=content,
        embedding=embedding if embedding is not None else [1.0, 0.0, 0.0, 0.0],
    )


@pytest.mark.parametrize("operation", ["upsert", "batch", "mixed_batch", "embedding", "payload"])
def test_rejected_write_keeps_previous_memory_and_later_writes_searchable(tmp_path, operation):
    async def run():
        config = _config(tmp_path)
        store = FaissVectorStore(config)
        await store.initialize()
        original = _node()
        await store.upsert(original)
        try:
            replacement = deepcopy(original)
            replacement.content = "rejected replacement"
            replacement.embedding = [0.0, 1.0, 0.0]  # Wrong dimension.
            before = (deepcopy(store._data), dict(store._idx_to_id), store._next_idx)
            if operation == "upsert":
                with pytest.raises(AssertionError):
                    await store.upsert(replacement)
            elif operation in ("batch", "mixed_batch"):
                nodes = [replacement]
                if operation == "mixed_batch":
                    nodes.insert(0, _node("valid part of rejected batch"))
                with pytest.raises((AssertionError, ValueError)):
                    await store.upsert_batch(nodes)
            elif operation == "embedding":
                assert not await store.update_embedding(original.node_id, replacement.embedding)
            else:
                assert not await store.update_payload(original.node_id, {
                    "content": replacement.content, "embedding": replacement.embedding,
                })

            hits = await store.search(original.embedding, user_id="test-user")
            assert [(hit["node_id"], hit["node"].content) for hit in hits] == [
                (original.node_id, "original"),
            ]
            assert (store._data, store._idx_to_id, store._next_idx) == before
            following = _node("next successful write", [0.0, 1.0, 0.0, 0.0])
            await store.upsert(following)
            hits = await store.search(following.embedding, user_id="test-user")
            assert hits[0]["node_id"] == following.node_id
            assert hits[0]["score"] == pytest.approx(1.0)
        finally:
            await store.close()

        reopened = FaissVectorStore(config)
        await reopened.initialize()
        try:
            hits = await reopened.search(original.embedding, user_id="test-user")
            assert {hit["node_id"] for hit in hits} == {original.node_id, following.node_id}
            assert hits[0]["node"].content == "original"
        finally:
            await reopened.close()

    asyncio.run(run())


def test_successful_batch_keeps_last_duplicate_and_valid_internal_ids(tmp_path):
    async def run():
        store = FaissVectorStore(_config(tmp_path))
        await store.initialize()
        try:
            first = _node()
            await store.upsert(first)
            replacement = deepcopy(first)
            replacement.content = "updated"
            replacement.embedding = [0.0, 1.0, 0.0, 0.0]
            other = _node("other")
            ids = await store.upsert_batch([first, other, replacement])
            assert ids == [first.node_id, other.node_id, first.node_id]
            hits = await store.search(replacement.embedding, user_id="test-user")
            assert len(hits) == 2
            assert hits[0]["node"].content == "updated"
            assert hits[0]["score"] == pytest.approx(1.0)
            assert store._next_idx == store._index.ntotal
        finally:
            await store.close()

    asyncio.run(run())
