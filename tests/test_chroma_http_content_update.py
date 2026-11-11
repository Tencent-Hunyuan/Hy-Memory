"""HTTP request preparation with real Chroma persistence behind the transport."""

import asyncio
import json as json_module
import threading
from types import SimpleNamespace
from uuid import UUID

import chromadb
from chromadb.api.fastapi import FastAPI
from chromadb.api.models.Collection import Collection
import pytest

from hy_memory.config import MemoryConfig
from hy_memory.data.vector_store_chroma import ChromaVectorStore
from hy_memory.models.memory import MemoryNode


@pytest.fixture
async def stores(tmp_path, monkeypatch):
    backing = chromadb.PersistentClient(path=str(tmp_path / "server"))
    requests = []

    def http_client(**_kwargs):
        # Keep Chroma's real Collection and HTTP request preparation; replace
        # only network I/O with calls to a real persistent Chroma server API.
        api = FastAPI.__new__(FastAPI)
        api.get_max_batch_size = backing.get_max_batch_size
        api.supports_base64_encoding = lambda: False

        def make_request(method, path, *, json):
            assert method == "post"
            body = json_module.loads(json_module.dumps(
                json, default=lambda value: value.tolist(),
            ))
            requests.append((path, body))
            parts = path.split("/")
            operation = getattr(backing._server, "_" + parts[-1])
            return operation(
                collection_id=UUID(parts[-2]), tenant=parts[2], database=parts[4],
                **body,
            )

        api._make_request = make_request

        def get_collection(**kwargs):
            actual = backing.get_or_create_collection(**kwargs)
            return Collection(client=api, model=actual._model)

        return SimpleNamespace(get_or_create_collection=get_collection)

    monkeypatch.setattr(chromadb, "HttpClient", http_client)
    config = MemoryConfig.from_dict({"vector_store": {
        "provider": "chroma", "host": "synthetic-chroma", "port": 8000,
        "collection_name": "http_content", "embedding_dims": 4,
        "chroma_lexical_enabled": True,
        "persist_directory": str(tmp_path / "unused-client-data"),
    }})
    opened = [ChromaVectorStore(config), ChromaVectorStore(config)]
    try:
        for store in opened:
            await store.initialize()
            assert store._lexical_index is None
        await opened[0].upsert(MemoryNode(
            node_id="node", user_id="u1", content="OLDTOKEN",
            embedding=[1.0, 0.0, 0.0, 0.0],
        ))
        requests.clear()
        yield opened, requests
        assert not (tmp_path / "unused-client-data").exists()
    finally:
        for store in opened:
            await store.close()


@pytest.mark.parametrize("vector_method", ["update_embedding", "update_payload"])
@pytest.mark.parametrize("first_writer", ["content", "vector"])
@pytest.mark.parametrize("separate_clients", [False, True])
async def test_http_content_cannot_restore_a_stale_vector(
    stores, monkeypatch, vector_method, first_writer, separate_clients,
):
    opened, _requests = stores
    content_store = opened[0]
    vector_store = opened[1] if separate_clients else content_store
    content_ready, release_content, vector_ready, release_vector = (
        threading.Event() for _ in range(4)
    )
    tasks = []
    original_get = content_store._collection.get
    original_request = content_store._collection._client._make_request
    new_vector = [0.0, 1.0, 0.0, 0.0]

    def pause_content():
        content_ready.set()
        if not release_content.wait(10):
            raise TimeoutError("content writer was not released")

    def read_then_pause(**kwargs):
        result = original_get(**kwargs)
        if kwargs.get("include") == ["embeddings"]:
            pause_content()  # Pins the old implementation's stale snapshot.
        return result

    def request_then_pause(method, path, *, json):
        if path.endswith("/update") and json.get("documents") is not None:
            pause_content()  # Pins the fixed implementation's partial update.
        return original_request(method, path, json=json)

    original_vector_update = vector_store._collection.update

    def paused_vector_update(**kwargs):
        if kwargs.get("documents") is None and kwargs.get("embeddings") is not None:
            vector_ready.set()
            if not release_vector.wait(10):
                raise TimeoutError("vector writer was not released")
        return original_vector_update(**kwargs)

    monkeypatch.setattr(content_store._collection, "get", read_then_pause)
    monkeypatch.setattr(content_store._collection._client, "_make_request", request_then_pause)
    monkeypatch.setattr(vector_store._collection, "update", paused_vector_update)

    async def write_vector():
        if vector_method == "update_embedding":
            return await vector_store.update_embedding("node", new_vector)
        return await vector_store.update_payload("node", {"embedding": new_vector})

    try:
        if first_writer == "vector":
            tasks.append(asyncio.create_task(write_vector()))
            assert await asyncio.to_thread(vector_ready.wait, 5)
        else:
            release_vector.set()
        content = asyncio.create_task(content_store.update_payload("node", {"content": "NEWTOKEN"}))
        tasks.append(content)
        assert await asyncio.to_thread(content_ready.wait, 5)
        if first_writer == "content":
            tasks.append(asyncio.create_task(write_vector()))
        release_vector.set()
        vector = tasks[0] if first_writer == "vector" else tasks[-1]
        assert await asyncio.wait_for(asyncio.shield(vector), 5)
        release_content.set()
        assert all(await asyncio.gather(*tasks))
        result = await content_store.get_by_id("node")
        assert result.content == "NEWTOKEN"
        assert result.embedding == new_vector
    finally:
        release_content.set()
        release_vector.set()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_http_content_update_neither_reads_nor_sends_embeddings(stores, monkeypatch):
    opened, requests = stores
    store = opened[0]

    def forbidden(*_args, **_kwargs):
        raise AssertionError("content-only updates must not read or generate vectors")

    with monkeypatch.context() as patch:
        patch.setattr(store._collection, "get", forbidden)
        patch.setattr(store._collection, "_embed_record_set", forbidden)
        assert await store.update_payload("node", {"content": "NEWTOKEN", "tags": ["new"]})
    assert len(requests) == 1
    path, body = requests[0]
    assert path.endswith("/update")
    assert body["embeddings"] is None
    assert body["documents"] == ["NEWTOKEN"]
    assert json_module.loads(body["metadatas"][0]["tags"]) == ["new"]
    result = await store.get_by_id("node")
    assert result.content == "NEWTOKEN"
    assert result.tags == ["new"]
    assert result.embedding == [1.0, 0.0, 0.0, 0.0]


async def test_http_explicit_content_and_vector_update_still_writes_both(stores):
    opened, requests = stores
    store = opened[0]
    vector = [0.0, 0.0, 1.0, 0.0]
    assert await store.update_payload("node", {"content": "NEWTOKEN", "embedding": vector})
    assert requests[-1][1]["embeddings"] == [vector]
    result = await store.get_by_id("node")
    assert result.content == "NEWTOKEN"
    assert result.embedding == vector
