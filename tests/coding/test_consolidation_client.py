"""Public sync/async SDK entry points with real loops, SQL and native vectors."""

import json
import os
import uuid
from contextlib import contextmanager
from copy import deepcopy

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.coding.consolidator import ExperienceConsolidator
from hy_memory.coding.repository_base import MemoryRepository
from hy_memory.coding.store import CodingMemoryStore
from hy_memory.coding.types import CodingMemory, ConsolidationScope
from ._helpers import MockLLMProvider
from .test_consolidator import response
from .test_consolidation_repository import seed


@pytest.fixture
def client(coding_config, mock_embed, monkeypatch, tmp_path):
    coding_config.cache.db_path = str(tmp_path / "cache.db")
    coding_config.history.enable = False
    coding_config.coding.enable = True
    coding_config.llm.api_key = "test-key"
    coding_config.llm.base_url = "http://127.0.0.1:1/v1"
    mock_embed.get_embedding_dims = lambda: 1536
    instance = HyMemoryClient(coding_config, mode="pro", embedding_service=mock_embed)
    instance._coding_llm_provider = MockLLMProvider(json.dumps(response()))
    yield instance
    instance.close()


def seed_client(client, scene="working"):
    async def prepare():
        await client._get_coding_writer(variant=scene)
        seed(client._store_for_variant(scene)._repository, **({"scene": "coding"} if scene == "coding" else {}))
    client._loop_thread.run(prepare())


def test_sync_entry_replay_trace_and_required_exact_scope(client):
    seed_client(client)
    result = client.consolidate(["s1", "s2", "s3"], user_id="u", agent_id="a",
                                workspace_id="ws", branch="main", request_id="first", language="zh")
    replay = client.consolidate(["s3", "s2", "s1", "s1"], user_id="u", agent_id="a",
                                workspace_id="ws", branch="main", request_id="different")
    assert result["success"] and replay["reused"]
    assert replay["consolidation_id"] == result["consolidation_id"]
    assert result["request_id"] == "first" and replay["request_id"] == "different"
    assert result["receipt"]["experience_ids"] == replay["receipt"]["experience_ids"]
    assert "runtime" in result and "event_loop" in result["runtime"]
    assert client.get_consolidation(result["consolidation_id"], user_id="u")["is_current"]
    assert client.get_consolidation(result["consolidation_id"], user_id="u", scene="coding") is None
    with pytest.raises(TypeError):
        client.consolidate(["s1", "s2"], user_id="u")
    with pytest.raises(ValueError):
        client.consolidate(["s1", "s2"], user_id="u", agent_id="a", workspace_id=None, branch=None)
    with pytest.raises(ValueError):
        client.consolidate(["s1", "s2"], user_id="u", workspace_id="ws", branch="main", scene="chat")
    assert len(client._coding_llm_provider.calls) == 1


async def test_async_entry_uses_owned_loop_and_variant_isolation(client):
    seed_client(client, "working")
    seed_client(client, "coding")
    working = await client.async_consolidate(["s1", "s2"], user_id="u", agent_id="a",
                                             workspace_id="ws", branch="main", scene="working")
    coding = await client.async_consolidate(["s1", "s2"], user_id="u", agent_id="a",
                                            workspace_id="ws", branch="main", scene="coding")
    assert working["consolidation_id"] != coding["consolidation_id"]
    receipt = await client.async_get_consolidation(coding["consolidation_id"], user_id="u", scene="coding")
    assert receipt["scope"]["scene"] == "coding"
    assert await client.async_get_consolidation(coding["consolidation_id"], user_id="u", scene="working") is None


def test_normal_search_list_get_metadata_and_source_edit_diagnostics(client):
    seed_client(client)
    result = client.consolidate(["s1", "s2", "s3"], user_id="u", agent_id="a", workspace_id="ws", branch="main")
    key = result["receipt"]["experience_ids"][0]
    found = client.search("avoid lock inversion", user_ids=["u"], scene="working",
                          working_experience_min_score=0, working_fact_min_score=0)
    memory = next(m for m in found["experiences"] if m["experience_id"] == key)
    assert memory["consolidation_id"] == result["consolidation_id"]
    assert memory["memory_at"] is None and memory["evidence_refs"]
    limited = client.search("avoid lock inversion", user_ids=["u"], session_ids=["s1", "s2"],
                            scene="working", working_experience_min_score=0, working_fact_min_score=0)
    assert not limited["experiences"]
    listed = client.list_memories(user_id="u", scene="working", workspace_id="ws")
    assert listed["experiences"][0]["consolidation_id"] == result["consolidation_id"]
    got = client.get(key, kind="experience", variant="working")
    assert got["evidence_refs"] == memory["evidence_refs"]
    store = client._store_for_variant("working")
    fact = store._repository.get_facts(["s3"], "u")[0]
    fact.statement = "edited input not used by conclusion"
    store._repository.upsert_fact(fact)
    assert not client.search("avoid lock inversion", user_ids=["u"], scene="working")["experiences"]
    receipt = client.get_consolidation(result["consolidation_id"], user_id="u")
    assert receipt["status"] == "needs_review"
    assert next(r for r in receipt["read_set"] if r["material_id"] == "s3")["status"] == "changed"
    assert client.delete(key, kind="experience", variant="working")["deleted_count"] == 1
    assert client.get_consolidation(result["consolidation_id"], user_id="u")["withdrawn_ids"] == [key]


def test_public_mutation_protects_consolidated_body(client):
    seed_client(client)
    result = client.consolidate(["s1", "s2"], user_id="u", agent_id="a", workspace_id="ws", branch="main")
    key = result["receipt"]["experience_ids"][0]
    store = client._store_for_variant("working")
    original = store._repository.get_experience(key, "u")
    with pytest.raises(ValueError):
        client._loop_thread.run(store.edit_experience(key, user_id="u", solution="overwrite"))
    with pytest.raises(ValueError):
        client._loop_thread.run(store.insert(deepcopy(original)))
    changed = deepcopy(original)
    changed.session_id = "s1"
    with pytest.raises(ValueError):
        client._loop_thread.run(store.update(changed))
    assert store._repository.get_experience(key, "u").solution == original.solution
    client.delete(key, kind="experience", variant="working")
    replay = client.consolidate(["s1", "s2"], user_id="u", agent_id="a", workspace_id="ws", branch="main")
    assert replay["receipt"]["experiences"] == []
    assert replay["receipt"]["withdrawn_ids"] == [key]


def test_cold_receipt_query_never_initializes_model_or_vectors(client, monkeypatch):
    seed_client(client)
    result = client.consolidate(["s1", "s2"], user_id="u", agent_id="a", workspace_id="ws", branch="main")
    store = client._coding_stores.pop("working")
    client._coding_writers.pop("working")
    client._loop_thread.run(store.close())

    def forbidden(*args, **kwargs):
        raise AssertionError("cold receipt query initialized model or vectors")

    monkeypatch.setattr("hy_memory.coding.store.create_vector_store", forbidden)
    monkeypatch.setattr("hy_memory.agent.llm_provider.LLMProvider", forbidden)
    receipt = client.get_consolidation(result["consolidation_id"], user_id="u")
    assert receipt["is_current"] and receipt["experiences"]
    assert client.get_consolidation("missing", user_id="u") is None
    assert client.get_consolidation(result["consolidation_id"], user_id="other") is None

    def unavailable(*args, **kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr("hy_memory.coding.repository_sqlite.SqliteMemoryRepository.initialize", unavailable)
    with pytest.raises(RuntimeError, match="database unavailable"):
        client.get_consolidation(result["consolidation_id"], user_id="u")


def test_call_embedding_override_and_language(client, monkeypatch):
    seed_client(client)
    seen = []

    @contextmanager
    def override(**kwargs):
        seen.append(kwargs)
        yield

    monkeypatch.setattr("hy_memory.client.embed_endpoint_override", override)
    client.consolidate(["s1", "s2"], user_id="u", agent_id="a", workspace_id="ws", branch="main",
                       embed_override={"base_url": "http://request-embedding.invalid"}, language="ja")
    assert seen[0]["base_url"] == "http://request-embedding.invalid"
    assert "選択されたセッション" in client._coding_llm_provider.calls[0]["prompt"]


async def test_old_custom_repository_remains_instantiable_and_rejects_before_model_or_vector(coding_store):
    # Implement precisely the pre-existing abstract contract; new methods are optional.
    attributes = {name: (lambda self, *args, **kwargs: None) for name in MemoryRepository.__abstractmethods__}
    LegacyRepository = type("LegacyRepository", (MemoryRepository,), attributes)
    legacy = LegacyRepository()
    provider = MockLLMProvider(json.dumps(response()))
    coding_store._repository.close()
    coding_store._repository = legacy
    consolidator = ExperienceConsolidator(coding_store, provider, coding_store.config)
    with pytest.raises(NotImplementedError, match="atomic consolidation"):
        await consolidator.consolidate(ConsolidationScope("u", "a", "default", None, None), ["s1", "s2"])
    assert provider.calls == []
    ordinary = CodingMemory("ordinary", "u", task="task", solution="solution")
    await coding_store.insert(ordinary)  # legacy contract still accepts ordinary writes


def test_token_budget_environment(monkeypatch):
    from hy_memory.config import CodingConfig
    monkeypatch.setenv("MEMORY_CODING_CONSOLIDATION_MAX_INPUT_TOKENS", "17000")
    monkeypatch.setenv("MEMORY_CODING_CONSOLIDATION_MAX_OUTPUT_TOKENS", "4200")
    config = CodingConfig()
    assert (config.consolidation_max_input_tokens, config.consolidation_max_output_tokens) == (17000, 4200)


@pytest.mark.parametrize("provider", ["chroma", "qdrant-local", "qdrant-remote", "faiss"])
async def test_native_dense_vector_channel_contract(repositories, tmp_path, mock_embed, provider):
    """Real adapters, each with both business backends; no model or embed network."""
    cfg, create, _ = repositories
    if provider.startswith("qdrant"):
        pytest.importorskip("qdrant_client")
    elif provider == "faiss":
        pytest.importorskip("faiss")
    if provider == "qdrant-remote" and not os.getenv("HY_MEMORY_TEST_QDRANT_PORT"):
        pytest.skip("Set HY_MEMORY_TEST_QDRANT_PORT for the disposable Qdrant Server contract")
    cfg.vector_store.provider = "qdrant" if provider.startswith("qdrant") else provider
    cfg.vector_store.persist_directory = str(tmp_path / provider)
    cfg.vector_store.collection_name = "consolidation_native_" + uuid.uuid4().hex[:12]
    cfg.vector_store.host = "127.0.0.1" if provider == "qdrant-remote" else None
    if provider == "qdrant-remote":
        cfg.vector_store.port = int(os.environ["HY_MEMORY_TEST_QDRANT_PORT"])
    cfg.vector_store.embedding_dims = 1536
    repo = create()
    scope = seed(repo)
    store = CodingMemoryStore(cfg, mock_embed, db_path=str(tmp_path / "memory.db"), repository=repo)
    try:
        if provider == "qdrant-local":
            # This contract exercises the existing experience collection only.
            # Baseline local Qdrant cannot open two independent clients on the
            # same persistence directory (experience + fact facade initialization).
            from hy_memory.data.vector_store import create_vector_store
            store._vec = create_vector_store(store._coding_config)
            await store._vec.initialize()
        else:
            await store.initialize()
        provider_stub = MockLLMProvider(json.dumps(response()))
        consolidator = ExperienceConsolidator(store, provider_stub, cfg)
        result = await consolidator.consolidate(scope, ["s1", "s2", "s3"])
        memory = result.receipt.experiences[0]
        query = await mock_embed.embed(memory.task)
        hits = await store.search_by_query_embedding(query, "u")
        assert [hit["experience_id"] for hit in hits] == [memory.experience_id]
        assert hits[0]["matched_key_text"] == memory.task
        assert await store.search_by_query_embedding(query, "u", session_ids=["s1", "s2"]) == []
        assert (await consolidator.consolidate(scope, ["s3", "s2", "s1"])).reused
        assert await store.delete(memory.experience_id, "u")
        assert await store.search_by_query_embedding(query, "u") == []
        assert repo.get_consolidation(result.consolidation_id, "u").withdrawn_ids == [memory.experience_id]
    finally:
        await store.close()
