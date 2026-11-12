"""A manual edit must become the newest update without rewriting event time."""

from datetime import datetime, timedelta

import pytest

from hy_memory import HyMemoryClient, MemoryConfig
from hy_memory.models.memory import MemoryLayer, MemoryNode
from tests.test_public_get_and_embedding_service import FakeEmbeddingService


@pytest.mark.parametrize("backend", ["faiss", "chroma", "qdrant"])
def test_update_refreshes_persisted_timestamp_and_recent_order(tmp_path, monkeypatch, backend):
    pytest.importorskip({"faiss": "faiss", "chroma": "chromadb", "qdrant": "qdrant_client"}[backend])
    if backend == "qdrant":
        from hy_memory.pipelines._retrieval import bm25_fastembed

        # Timestamp persistence does not need a downloaded sparse embedding model.
        monkeypatch.setattr(bm25_fastembed, "is_available", lambda: False)
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMORY_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "false")
    monkeypatch.setenv("MEMORY_PIPELINE_LOG_ENABLED", "false")
    monkeypatch.setenv("MEMORY_ENABLE_GRAPH", "false")
    config = MemoryConfig.from_dict({
        "vector_store": {
            "provider": backend,
            "collection_name": "test_edit_timestamp",
            "persist_directory": str(tmp_path / "vectors"),
            "path": str(tmp_path / "qdrant"),
            "embedding_dims": 8,
        },
        "embedder": {"embedding_dims": 8},
        "cache": {"backend": "sqlite", "sqlite_path": str(tmp_path / "cache.db")},
        "history": {"enable": False},
        "coding": {"enable": False, "db_path": str(tmp_path / "coding.db")},
    })
    now = datetime.now().replace(microsecond=0)
    created = now - timedelta(days=30)
    event_time = created - timedelta(days=10)
    old = MemoryNode(
        node_id="edited", user_id="u", content="before edit",
        layer=MemoryLayer.L2_FACT, embedding=[1.0] + [0.0] * 7,
        gmt_created=created, gmt_modified=created, memory_at=event_time,
    )
    newer = MemoryNode(
        node_id="newer", user_id="u", content="more recently created",
        layer=MemoryLayer.L2_FACT, embedding=[1.0] + [0.0] * 7,
        gmt_created=now - timedelta(days=1), gmt_modified=now - timedelta(days=1),
    )
    with HyMemoryClient(config, embedding_service=FakeEmbeddingService(8)) as client:
        client._loop_thread.run(client._vector_store.upsert_batch([old, newer]))
        before = client.list_memories(user_id="u", scene="all")
        assert [item["memory_id"] for item in before["memories"]] == ["newer", "edited"]

        lower_bound = int(datetime.now().timestamp())
        assert client.update("edited", "after edit", kind="chat")["success"] is True
        upper_bound = int(datetime.now().timestamp())

        saved = client.get("edited", kind="chat")
        assert saved["content"] == "after edit"
        assert saved["gmt_created"] == int(created.timestamp())
        assert saved["memory_at"] == int(event_time.timestamp())
        stored = client._loop_thread.run(client._vector_store.get_by_id("edited"))
        modified = int(stored.gmt_modified.timestamp())
        assert lower_bound <= modified <= upper_bound
        page = client.list_memories(user_id="u", scene="all", limit=1)
        assert page["memories"][0]["memory_id"] == "edited"
        assert page["memories"][0]["updated_at"] == modified
        stats = client.memory_stats(user_id="u")
        assert stats["scenes"]["chat"]["latest_updated_at"] == modified

    with HyMemoryClient(config, embedding_service=FakeEmbeddingService(8)) as reopened:
        stored = reopened._loop_thread.run(reopened._vector_store.get_by_id("edited"))
        assert int(stored.gmt_modified.timestamp()) == modified


def test_update_syncs_existing_kuzu_content_without_creating_nodes(tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    pytest.importorskip("kuzu")
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMORY_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "false")
    monkeypatch.setenv("MEMORY_PIPELINE_LOG_ENABLED", "false")
    monkeypatch.setenv("MEMORY_ENABLE_GRAPH", "true")
    config = MemoryConfig.from_dict({
        "vector_store": {
            "provider": "chroma",
            "collection_name": "test_edit_graph",
            "persist_directory": str(tmp_path / "vectors"),
            "embedding_dims": 8,
        },
        "embedder": {"embedding_dims": 8},
        "graph_store": {
            "provider": "kuzu",
            "db_path": str(tmp_path / "graph"),
            "buffer_pool_mb": 128,
            "max_threads": 2,
            "memory_graph_write": True,
        },
        "cache": {"backend": "sqlite", "sqlite_path": str(tmp_path / "cache.db")},
        "history": {"enable": False},
        "coding": {"enable": False, "db_path": str(tmp_path / "coding.db")},
    })
    projected = MemoryNode(
        node_id="projected", user_id="u", content="before edit", tags=["tea"],
        layer=MemoryLayer.L2_FACT, embedding=[1.0] + [0.0] * 7,
    )
    vector_only = MemoryNode(
        node_id="vector-only", user_id="u", content="before edit",
        layer=MemoryLayer.L2_FACT, embedding=[1.0] + [0.0] * 7,
    )
    with HyMemoryClient(config, mode="pro", embedding_service=FakeEmbeddingService(8)) as client:
        run = client._loop_thread.run
        graph = client._graph_store
        run(client._vector_store.upsert_batch([projected, vector_only]))
        run(graph.upsert_memory_node(projected))
        assert client.update(projected.node_id, "after edit", kind="chat")["success"]
        assert client.get(projected.node_id, kind="chat")["content"] == "after edit"
        stored = run(graph.get_node(projected.node_id))
        assert stored.content == "after edit"
        assert stored.tags == ["tea"]

        assert client.update(vector_only.node_id, "after edit", kind="chat")["success"]
        assert run(graph.get_node(vector_only.node_id)) is None

        async def fail_update(_node_id, _updates):
            return False

        with monkeypatch.context() as patch:
            patch.setattr(graph, "update_node", fail_update)
            receipt = client.update(projected.node_id, "saved to vector only", kind="chat")
        assert receipt["success"] is True
        assert receipt["graph_sync_failed"] is True
        assert client.get(projected.node_id, kind="chat")["content"] == "saved to vector only"
        assert run(graph.get_node(projected.node_id)).content == "after edit"

    with HyMemoryClient(config, mode="pro", embedding_service=FakeEmbeddingService(8)) as reopened:
        graph = reopened._graph_store
        stored = reopened._loop_thread.run(graph.get_node(projected.node_id))
        assert stored.content == "after edit"
