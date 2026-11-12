"""Manual edits must finish embedding before changing durable memory state."""

import asyncio
from datetime import datetime

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.coding.store import CodingMemoryStore
from hy_memory.coding.types import CodingMemory, CodingWriteError, WorkingFact


class _Embedder:
    failure = None

    def __init__(self):
        self.calls = []

    def _vectors(self, texts):
        self.calls.append(list(texts))
        if self.failure == "exception":
            raise ConnectionError("synthetic embedding outage")
        if self.failure == "cancel":
            raise asyncio.CancelledError()
        if self.failure == "empty":
            return [[] for _ in texts]
        if self.failure == "incomplete":
            return [[0.0, 1.0, 0.0]]
        return [
            [0.0, 1.0, 0.0] if "new" in text else [1.0, 0.0, 0.0]
            for text in texts
        ]

    async def embed(self, text):
        return self._vectors([text])[0]

    async def embed_batch(self, texts):
        return self._vectors(texts)


@pytest.fixture(
    params=[("chroma", "working"), ("chroma", "coding"),
            ("faiss", "working"), ("faiss", "coding")],
    ids=["chroma-working", "chroma-coding", "faiss-working", "faiss-coding"],
)
async def edit_store(request, coding_config):
    provider, variant = request.param
    pytest.importorskip({"chroma": "chromadb", "faiss": "faiss"}[provider])
    coding_config.vector_store.provider = provider
    coding_config.vector_store.embedding_dims = 3
    embedder = _Embedder()
    store = CodingMemoryStore(coding_config, embedder, variant=variant)
    await store.initialize()
    try:
        yield store, embedder
    finally:
        await store.close()


async def _public_update(store, memory_id, content, kind, **kwargs):
    # Match the existing all-scenes tests: real namespace resolution and update
    # methods, without starting unrelated model clients or background services.
    client = object.__new__(HyMemoryClient)
    client._coding_stores = {store.variant: store}

    async def no_log(**kwargs):
        pass

    client._maybe_store_op_log = no_log
    return await HyMemoryClient.async_update.__wrapped__(
        client, memory_id, content, kind=kind, variant=store.variant, **kwargs,
    )


async def _seed_experience(store):
    await store.insert(CodingMemory(
        experience_id="e1", user_id="u", task="old task",
        search_keys=["old key"], solution="old solution",
        memory_at=datetime(2020, 1, 1),
    ))
    return (await store.get_by_id("e1")).to_dict()


@pytest.mark.parametrize("failure", ["exception", "empty", "incomplete", "cancel"])
async def test_experience_edit_failure_preserves_content_and_recall(edit_store, failure):
    store, embedder = edit_store
    original = await _seed_experience(store)
    original_vectors = await store._vec.get_embeddings(["e1:0", "e1:1"])
    embedder.failure = failure

    error = None
    try:
        await _public_update(store, "e1", "new solution", "experience", task="new task")
    except (CodingWriteError, asyncio.CancelledError) as exc:
        error = exc

    assert (await store.get_by_id("e1")).to_dict() == original
    assert await store._vec.get_embeddings(["e1:0", "e1:1"]) == original_vectors
    hits = await store.search_by_query_embedding([1.0, 0.0, 0.0], user_id="u")
    assert [hit["experience_id"] for hit in hits] == ["e1"]
    assert isinstance(error, asyncio.CancelledError if failure == "cancel" else CodingWriteError)
    if isinstance(error, CodingWriteError):
        assert error.error_code == "EMBEDDING_FAILED"


@pytest.mark.parametrize("failure", ["exception", "empty", "cancel"])
async def test_fact_edit_failure_preserves_content_and_recall(edit_store, failure):
    store, embedder = edit_store
    await store.insert_fact(WorkingFact(
        fact_id="f1", user_id="u", statement="old statement", subject="subject",
        details="unchanged details", memory_at=datetime(2020, 1, 1),
    ))
    original = (await store.get_fact("f1")).to_dict()
    original_vectors = await store._fact_vec.get_embeddings(["f1"])
    embedder.failure = failure

    error = None
    try:
        await _public_update(store, "f1", "new statement", "fact")
    except (CodingWriteError, asyncio.CancelledError) as exc:
        error = exc

    assert (await store.get_fact("f1")).to_dict() == original
    assert await store._fact_vec.get_embeddings(["f1"]) == original_vectors
    hits = await store.search_facts([1.0, 0.0, 0.0], user_id="u")
    assert [fact.fact_id for fact, _ in hits] == ["f1"]
    assert isinstance(error, asyncio.CancelledError if failure == "cancel" else CodingWriteError)
    if isinstance(error, CodingWriteError):
        assert error.error_code == "EMBEDDING_FAILED"


async def test_solution_only_edit_does_not_need_embedding(edit_store):
    store, embedder = edit_store
    original = await _seed_experience(store)
    embedder.calls.clear()
    embedder.failure = "exception"

    result = await _public_update(store, "e1", "new solution", "experience", task="old task")

    assert result["success"] is True
    stored = (await store.get_by_id("e1")).to_dict()
    assert stored["solution"] == "new solution"
    assert stored["task"] == original["task"]
    assert stored["memory_at"] == original["memory_at"]
    assert stored["updated_at"] >= original["updated_at"]
    assert embedder.calls == []


async def test_search_key_only_failure_preserves_old_keys(edit_store):
    store, embedder = edit_store
    original = await _seed_experience(store)
    original_vectors = await store._vec.get_embeddings(["e1:0", "e1:1"])
    embedder.failure = "exception"

    with pytest.raises(CodingWriteError, match="synthetic embedding outage"):
        await store.edit_experience("e1", search_keys=["new key"])

    assert (await store.get_by_id("e1")).to_dict() == original
    assert await store._vec.get_embeddings(["e1:0", "e1:1"]) == original_vectors


async def test_superseded_fact_edit_does_not_reintroduce_vectors(edit_store):
    store, embedder = edit_store
    await store.insert_fact(WorkingFact(fact_id="f1", user_id="u", statement="old statement"))
    await store.supersede_fact("f1", "successor")
    embedder.calls.clear()
    embedder.failure = "exception"

    result = await _public_update(store, "f1", "new statement", "fact")

    assert result["success"] is True
    assert (await store.get_fact("f1")).statement == "new statement"
    assert await store._fact_vec.get_by_id("f1") is None
    assert embedder.calls == []


async def test_successful_edits_embed_once_and_replace_search_vectors(edit_store):
    store, embedder = edit_store
    original = await _seed_experience(store)
    await store.insert_fact(WorkingFact(fact_id="f1", user_id="u", statement="old statement"))
    embedder.calls.clear()

    updated = await store.edit_experience("e1", task="new task", search_keys=[])
    result = await _public_update(store, "f1", "new statement", "fact")

    assert result["success"] is True
    assert updated.memory_at == datetime(2020, 1, 1)
    assert updated.created_at.isoformat() == original["created_at"]
    assert embedder.calls == [["new task"], ["new statement"]]
    assert await store._vec.get_by_id("e1:1") is None
    experiences = await store.search_by_query_embedding([0.0, 1.0, 0.0], user_id="u")
    facts = await store.search_facts([0.0, 1.0, 0.0], user_id="u", min_score=0.99)
    assert experiences[0]["score"] == pytest.approx(1.0)
    assert [(fact.fact_id, score) for fact, score in facts] == [("f1", pytest.approx(1.0))]
