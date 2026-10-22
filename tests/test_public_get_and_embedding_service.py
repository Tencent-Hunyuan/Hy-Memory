from __future__ import annotations

import asyncio
import time

import pytest

from hy_memory import (
    AmbiguousMemoryIdError,
    EmbeddingDimensionMismatchError,
    EmbeddingService,
    HyMemoryClient,
)
from hy_memory.coding.types import CodingMemory, WorkingFact
from hy_memory.coding.writer import CodingWriter
from hy_memory.config import MemoryConfig
from hy_memory.models.memory import MemoryLayer, MemoryNode
from hy_memory.pipelines import ComponentFactory


class FakeEmbeddingService:
    def __init__(self, dims: int):
        self.dims = dims
        self.close_calls = 0

    async def embed(self, text, use_cache=True):
        return [0.0] * self.dims

    async def embed_with_meta(self, text, use_cache=True):
        return await self.embed(text, use_cache), {}

    async def embed_batch(self, texts, use_cache=True):
        return [[0.0] * self.dims for _ in texts]

    async def embed_queued(self, text, use_cache=True):
        return await self.embed(text, use_cache)

    async def embed_queued_with_meta(self, text, use_cache=True):
        return await self.embed(text, use_cache), {}

    def get_embedding_dims(self):
        return self.dims

    async def close(self):
        self.close_calls += 1


class ExactStore:
    def __init__(self, experience=None, fact=None):
        self.experience = experience
        self.fact = fact

    async def get_by_id(self, memory_id):
        if self.experience and self.experience.experience_id == memory_id:
            return self.experience
        return None

    async def get_fact(self, memory_id):
        if self.fact and self.fact.fact_id == memory_id:
            return self.fact
        return None


class ChatStore:
    def __init__(self, node=None):
        self.node = node

    async def get_by_id(self, memory_id):
        if self.node and self.node.node_id == memory_id:
            return self.node
        return None


def exact_client(*, node=None, stores=None, config=None):
    client = HyMemoryClient.__new__(HyMemoryClient)
    client._vector_store = ChatStore(node)
    client._graph_store = None
    client._coding_stores = stores or {}
    if config is not None:
        client._config = config
    client._backend_identity = lambda: {}

    async def no_op_log(**_kwargs):
        return None

    client._maybe_store_op_log = no_op_log
    return client


@pytest.mark.asyncio
async def test_exact_get_supports_all_public_namespaces_and_chat_metadata():
    chat = MemoryNode(
        node_id="chat-1",
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        owner="user",
        layer=MemoryLayer.L2_FACT,
        content="chat content",
    )
    experience = CodingMemory(
        experience_id="exp-1",
        user_id="u1",
        agent_id="a1",
        session_id="s2",
        task="ship",
        solution="run release",
        boundary_envs="python>=3.9",
    )
    fact = WorkingFact(
        fact_id="fact-1",
        user_id="u1",
        agent_id="a1",
        session_id="s3",
        statement="release completed",
        details="tag=v1.2.3",
    )
    client = exact_client(
        node=chat,
        stores={"working": ExactStore(experience, fact)},
    )
    raw_get = HyMemoryClient.async_get.__wrapped__

    chat_result = await raw_get(client, "chat-1")
    assert chat_result["kind"] == "chat"
    assert chat_result["owner"] == "user"
    assert chat_result["session_id"] == "s1"

    exp_result = await raw_get(client, "exp-1")
    assert exp_result["memory_id"] == "exp-1"
    assert exp_result["kind"] == "experience"
    assert exp_result["variant"] == "working"
    assert exp_result["boundary_envs"] == "python>=3.9"

    fact_result = await raw_get(client, "fact-1")
    assert fact_result["memory_id"] == "fact-1"
    assert fact_result["kind"] == "fact"
    assert fact_result["variant"] == "working"
    assert fact_result["details"] == "tag=v1.2.3"


@pytest.mark.asyncio
async def test_exact_get_collision_fails_closed_and_can_be_disambiguated():
    working = CodingMemory(
        experience_id="same-id", user_id="u1", task="working", solution="w"
    )
    coding = CodingMemory(
        experience_id="same-id", user_id="u1", task="coding", solution="c"
    )
    client = exact_client(
        stores={
            "working": ExactStore(experience=working),
            "coding": ExactStore(experience=coding),
        }
    )
    raw_get = HyMemoryClient.async_get.__wrapped__

    with pytest.raises(AmbiguousMemoryIdError) as raised:
        await raw_get(client, "same-id")
    assert raised.value.namespaces == (
        "working:experience",
        "coding:experience",
    )

    result = await raw_get(
        client, "same-id", kind="experience", variant="coding"
    )
    assert result["task"] == "coding"


@pytest.mark.asyncio
async def test_cold_exact_get_does_not_create_missing_coding_database(tmp_path):
    config = MemoryConfig.from_env()
    config.coding.db_path = str(tmp_path / "coding_memory.db")
    client = exact_client(config=config)

    async def should_not_initialize(*_args, **_kwargs):
        raise AssertionError("missing database must not initialize a coding store")

    client._get_coding_writer = should_not_initialize
    result = await HyMemoryClient.async_get.__wrapped__(
        client, "missing", kind="fact"
    )

    assert result is None
    assert list(tmp_path.iterdir()) == []


def test_embedding_service_is_structural_and_dimension_mismatch_fails_fast():
    config = MemoryConfig.from_env()
    config.vector_store.embedding_dims = 4
    service = FakeEmbeddingService(3)
    assert isinstance(service, EmbeddingService)

    client = HyMemoryClient.__new__(HyMemoryClient)
    client._config = config
    with pytest.raises(EmbeddingDimensionMismatchError):
        client._validate_embedding_service(service)


def test_coding_write_receipt_identifies_variant():
    writer = CodingWriter.__new__(CodingWriter)
    writer.variant = "coding"
    receipt = writer._empty_response("request-1", time.perf_counter())
    assert receipt["variant"] == "coding"


@pytest.mark.asyncio
async def test_component_factory_borrows_injected_embedding_service():
    config = MemoryConfig.from_env()
    config.vector_store.embedding_dims = 3
    service = FakeEmbeddingService(3)
    factory = ComponentFactory(config, embedding_service=service)

    assert factory._get_shared_embed_service() is service
    await factory.close()
    assert service.close_calls == 0


def test_constructor_and_from_config_accept_embedding_service_before_init(
    monkeypatch,
):
    seen = []

    async def fake_initialize(self):
        seen.append(self._borrowed_embedding_service)
        self._embed_service = self._borrowed_embedding_service
        self._registry = ComponentFactory(
            self._config, embedding_service=self._embed_service
        )

    class InlineLoop:
        def __init__(self):
            self._loop = None

        def run(self, coroutine):
            return asyncio.run(coroutine)

        def stop(self):
            pass

    monkeypatch.setattr(HyMemoryClient, "_initialize", fake_initialize)
    monkeypatch.setattr("hy_memory.client._LoopThread", InlineLoop)

    config = MemoryConfig.from_env()
    config.vector_store.embedding_dims = 3
    service = FakeEmbeddingService(3)
    direct = HyMemoryClient(config, mode="lite", embedding_service=service)
    configured = HyMemoryClient.from_config(
        {
            "mode": "lite",
            "vector_store": {"embedding_dims": 3},
            "embedder": {"embedding_dims": 3},
        },
        embedding_service=service,
    )

    assert seen == [service, service]
    assert direct._registry._get_shared_embed_service() is service
    assert configured._registry._get_shared_embed_service() is service
    assert service.close_calls == 0
