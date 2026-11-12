"""Content-only updates preserve vectors on real local and HTTP Chroma stores."""

import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.data.vector_store_chroma import ChromaVectorStore
from hy_memory.models.memory import MemoryNode


OLD_VECTOR = [1.0, 0.0, 0.0, 0.0]
NEW_VECTOR = [0.0, 1.0, 0.0, 0.0]


@pytest.fixture(scope="module")
def chroma_server(tmp_path_factory):
    """Start an isolated real HTTP server; never use a configured user server."""
    directory = tmp_path_factory.mktemp("chroma-content-server")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    command = [
        sys.executable, "-c",
        "import sys; from chromadb.cli.cli import app; sys.argv[0] = 'chroma'; app()",
        "run", "--host", "127.0.0.1", "--port", str(port),
        "--path", str(directory / "data"),
    ]
    environment = dict(os.environ, ANONYMIZED_TELEMETRY="False")
    with (directory / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command, stdout=log, stderr=subprocess.STDOUT, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    pytest.fail(f"Chroma server exited with {process.returncode}; see {log.name}")
                try:
                    with urlopen(f"http://127.0.0.1:{port}/api/v2/heartbeat", timeout=1):
                        break
                except (URLError, TimeoutError):
                    if time.monotonic() >= deadline:
                        pytest.fail(f"Chroma server did not start; see {log.name}")
                    time.sleep(0.1)
            yield port
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


@pytest.fixture(params=["local", pytest.param("http", marks=pytest.mark.integration)])
async def stores(request, tmp_path):
    settings = {
        "provider": "chroma", "collection_name": "content_" + uuid4().hex,
        "persist_directory": str(tmp_path / "chroma"), "embedding_dims": 4,
        "host": None, "port": None,  # Override remote settings inherited from the environment.
    }
    if request.param == "http":
        settings.update(host="127.0.0.1", port=request.getfixturevalue("chroma_server"))
    config = MemoryConfig.from_dict({"vector_store": settings})
    opened = [ChromaVectorStore(config), ChromaVectorStore(config)]
    try:
        for store in opened:
            await store.initialize()
        await opened[0].upsert(MemoryNode(
            node_id="node", user_id="synthetic-user", content="OLDTOKEN",
            embedding=list(OLD_VECTOR), tags=["old"],
        ))
        yield opened
    finally:
        for store in opened:
            client = store._client
            await store.close()
            if client is not None:
                client.close()


def forbid_embedding(*_args, **_kwargs):
    raise AssertionError("payload updates must not generate an embedding")


@pytest.fixture
def remote_vector_environment(monkeypatch, tmp_path):
    """Reject HTTP clients while recording real local client construction."""
    import chromadb

    monkeypatch.setenv("MEMORY_VECTOR_HOST", "configured-remote.invalid")
    monkeypatch.setenv("MEMORY_VECTOR_PORT", "18000")
    original_persistent_client = chromadb.PersistentClient
    paths = []

    def persistent_client(*, path):
        assert path == str(tmp_path / "chroma")
        paths.append(path)
        return original_persistent_client(path=path)

    def forbid_http_client(*_args, **_kwargs):
        raise AssertionError("local tests must not connect to a configured remote server")

    monkeypatch.setattr(chromadb, "PersistentClient", persistent_client)
    monkeypatch.setattr(chromadb, "HttpClient", forbid_http_client)
    return paths


@pytest.mark.parametrize("stores", ["local"], indirect=True)
async def test_local_stores_ignore_remote_environment(remote_vector_environment, stores, tmp_path):
    assert remote_vector_environment == [str(tmp_path / "chroma")] * 2
    for store in stores:
        assert store.config.vector_store.host is None
        assert store.config.vector_store.port is None
        assert (await store.get_by_id("node")).embedding == OLD_VECTOR


async def test_content_only_preserves_vector_and_updates_document(stores, monkeypatch):
    store = stores[0]
    updates = {"content": "NEWTOKEN", "tags": ["new", "synthetic"]}
    with monkeypatch.context() as patch:
        patch.setattr(store._collection, "_embed_record_set", forbid_embedding)
        patch.setattr(store._collection, "get", forbid_embedding)
        assert await store.update_payload("node", updates)
    result = await store.get_by_id("node")
    assert result.content == "NEWTOKEN"
    assert result.embedding == OLD_VECTOR
    assert result.user_id == "synthetic-user"
    assert result.tags == ["new", "synthetic"]
    assert updates == {"content": "NEWTOKEN", "tags": ["new", "synthetic"]}
    for token, expected in [("OLDTOKEN", []), ("NEWTOKEN", ["NEWTOKEN"])]:
        records = store._collection.get(where_document={"$contains": token})
        assert records["documents"] == expected


@pytest.mark.parametrize("updates,content,vector,tags", [
    ({}, "OLDTOKEN", OLD_VECTOR, ["old"]),
    ({"tags": ["new"]}, "OLDTOKEN", OLD_VECTOR, ["new"]),
    ({"embedding": NEW_VECTOR}, "OLDTOKEN", NEW_VECTOR, ["old"]),
    ({"content": "NEW", "embedding": NEW_VECTOR}, "NEW", NEW_VECTOR, ["old"]),
])
async def test_other_update_modes(stores, monkeypatch, updates, content, vector, tags):
    store = stores[0]
    monkeypatch.setattr(store._collection, "_embed_record_set", forbid_embedding)
    assert await store.update_payload("node", updates)
    result = await store.get_by_id("node")
    assert result.content == content
    assert result.embedding == vector
    assert result.tags == tags


async def test_empty_content_clears_document_without_changing_vector(stores, monkeypatch):
    store = stores[0]
    monkeypatch.setattr(store._collection, "_embed_record_set", forbid_embedding)
    assert await store.update_payload("node", {"content": ""})
    records = store._collection.get(include=["documents", "metadatas", "embeddings"])
    assert records["documents"] == [""]
    assert records["metadatas"][0]["content"] == ""
    assert records["embeddings"].tolist() == [OLD_VECTOR]


@pytest.mark.parametrize("content", [None, 123, ["invalid"]])
async def test_invalid_content_is_rejected_without_changes(stores, content):
    store = stores[0]
    assert not await store.update_payload("node", {"content": content})
    result = await store.get_by_id("node")
    assert result.content == "OLDTOKEN"
    assert result.embedding == OLD_VECTOR


async def test_unknown_id_remains_a_noop(stores, monkeypatch):
    store = stores[0]
    monkeypatch.setattr(store._collection, "_embed_record_set", forbid_embedding)
    assert await store.update_payload("unknown", {"content": "NEW"})
    assert await store.get_by_id("unknown") is None


@pytest.mark.parametrize("vector_method", ["update_embedding", "update_payload"])
@pytest.mark.parametrize("first_writer", ["content", "vector"])
@pytest.mark.parametrize("first_completion", ["content", "vector"])
@pytest.mark.parametrize("separate_clients", [False, True])
async def test_content_cannot_restore_a_concurrently_updated_vector(
    stores, monkeypatch, vector_method, first_writer, first_completion, separate_clients,
):
    content_store = stores[0]
    vector_store = stores[1] if separate_clients else content_store
    content_ready, release_content, vector_ready, release_vector = (
        threading.Event() for _ in range(4)
    )
    tasks = []
    original_get = content_store._collection.get
    original_update = content_store._collection.update
    original_partial = content_store._collection._client._update
    original_vector_update = vector_store._collection.update

    def pause_content():
        content_ready.set()
        if not release_content.wait(10):
            raise TimeoutError("content writer was not released")

    def get_then_pause(**kwargs):
        result = original_get(**kwargs)
        if kwargs.get("include") == ["embeddings"]:
            pause_content()  # Exposes an unsafe read/restore implementation.
        return result

    def collection_update(**kwargs):
        if kwargs.get("documents") is not None:
            pause_content()  # Original public API would regenerate the vector.
        elif kwargs.get("embeddings") is not None:
            vector_ready.set()
            if not release_vector.wait(10):
                raise TimeoutError("vector writer was not released")
        return original_update(**kwargs)

    def partial_update(**kwargs):
        if kwargs.get("documents") is not None:
            pause_content()  # Fixed path: no vector is read or sent.
        return original_partial(**kwargs)

    def vector_update(**kwargs):
        vector_ready.set()
        if not release_vector.wait(10):
            raise TimeoutError("vector writer was not released")
        return original_vector_update(**kwargs)

    monkeypatch.setattr(content_store._collection, "get", get_then_pause)
    monkeypatch.setattr(content_store._collection, "update", collection_update)
    monkeypatch.setattr(content_store._collection._client, "_update", partial_update)
    monkeypatch.setattr(content_store._collection, "_embed_record_set", forbid_embedding)
    if separate_clients:
        monkeypatch.setattr(vector_store._collection, "update", vector_update)

    async def write_vector():
        if vector_method == "update_embedding":
            return await vector_store.update_embedding("node", NEW_VECTOR)
        return await vector_store.update_payload("node", {"embedding": NEW_VECTOR})

    try:
        if first_writer == "vector":
            vector = asyncio.create_task(write_vector())
            tasks.append(vector)
            assert await asyncio.to_thread(vector_ready.wait, 5)
        content = asyncio.create_task(content_store.update_payload("node", {"content": "NEW"}))
        tasks.append(content)
        assert await asyncio.to_thread(content_ready.wait, 5)
        if first_writer == "content":
            vector = asyncio.create_task(write_vector())
            tasks.append(vector)
        if first_completion == "content":
            release_content.set()
            assert await asyncio.wait_for(asyncio.shield(content), 5)
            release_vector.set()
        else:
            release_vector.set()
            assert await asyncio.wait_for(asyncio.shield(vector), 5)
            release_content.set()
        assert all(await asyncio.gather(*tasks))
        result = await content_store.get_by_id("node")
        assert result.content == "NEW"
        assert result.embedding == NEW_VECTOR
    finally:
        release_content.set()
        release_vector.set()
        await asyncio.gather(*tasks, return_exceptions=True)
