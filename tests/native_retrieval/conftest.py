"""Local native fixtures. Integration uses only an explicit dedicated Server URL."""

import asyncio
import json
import os
import time
import uuid
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from hy_memory.agent.llm_provider import LLMResponse
from hy_memory.client import HyMemoryClient
from hy_memory.config import MemoryConfig
from hy_memory.data.vector_store_qdrant import QdrantVectorStore
from hy_memory.models.memory import MemoryLayer, MemoryNode
from hy_memory.pipelines.registry import ComponentFactory


class FixedEmbed:
    def __init__(self):
        self.calls = []

    async def embed(self, text, **kwargs):
        self.calls.append(text)
        return [1.0, 0.0]

    async def embed_batch(self, texts, **kwargs):
        return [await self.embed(text) for text in texts]


class CaptureLLM:
    def __init__(self, ops=None):
        self.prompts = []
        self.ops = ops or []

    async def complete(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return LLMResponse(content=json.dumps(self.ops), tokens_used=1)


def node(content="ordinary", *, node_id=None, **kwargs):
    return MemoryNode(node_id=node_id or uuid.uuid4().hex, content=content,
                      user_id=kwargs.pop("user_id", "alice"),
                      agent_id=kwargs.pop("agent_id", "a"),
                      session_id=kwargs.pop("session_id", "s"),
                      layer=kwargs.pop("layer", MemoryLayer.L2_FACT),
                      embedding=kwargs.pop("embedding", [1.0, 0.0]), **kwargs)


def public_client(store, embed=None):
    client = HyMemoryClient.__new__(HyMemoryClient)
    client._config = store.config
    client._mode = "pro"
    client._registry = ComponentFactory(store.config, embedding_service=embed or FixedEmbed())
    client._registry._shared_vector_store = store
    client._registry._shared_vector_store_initialized = True
    client._vector_store = store
    client._cache = None
    client._history_store = None
    client._coding_llm_provider = CaptureLLM()
    client._search_loop_thread = SimpleNamespace(_loop=asyncio.get_running_loop())
    return client


@pytest.fixture
async def server_store():
    from qdrant_client import QdrantClient
    url = os.environ.get("HY_MEMORY_TEST_QDRANT_URL")
    if not url:
        pytest.fail("integration requires dedicated HY_MEMORY_TEST_QDRANT_URL")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        pytest.fail("HY_MEMORY_TEST_QDRANT_URL must be a Server URL")
    client = QdrantClient(url=url, timeout=5)
    deadline = time.monotonic() + 20
    while True:
        try:
            info = client.info()
            break
        except Exception:
            if time.monotonic() >= deadline:
                client.close()
                pytest.fail("dedicated Qdrant Server did not become ready within 20s")
            await asyncio.sleep(.1)
    assert info.version == "1.20.0", f"uncertified test Server {info.version}"
    config = MemoryConfig()
    config.coding.enable = False
    config.llm.enable_query_rewrite = False
    config.vector_store.embedding_dims = 2
    config.vector_store.collection_name = "hy_native_test_" + uuid.uuid4().hex
    store = QdrantVectorStore(config)
    # The only connection is the explicit test URL, never config/from_env.
    store._client = client
    from qdrant_client import models as m
    client.create_collection(store._collection_name, vectors_config=m.VectorParams(size=2, distance=m.Distance.COSINE))
    try:
        for field in ("user_id", "agent_id", "session_id", "isolation_key", "source", "layer", "status"):
            client.create_payload_index(store._collection_name, field, field_schema="keyword", wait=True)
        await store.prepare_native_retrieval()
        yield store
    finally:
        client.delete_collection(store._collection_name)
        client.close()


@pytest.fixture
async def populated(server_store):
    store = server_store
    target = node("ECONNRESET reconnectmarker src/network/reconnect.py 中文关键词", embedding=[0.0, 1.0])
    distractors = [node(f"unrelated distractor {i}") for i in range(35)]
    await store.upsert_batch(distractors + [target])
    return store, target
