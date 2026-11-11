"""Local lexical candidates reach the public SDK, without a model service."""

from datetime import datetime, timezone
import math
import uuid

import pytest

from hy_memory import HyMemoryClient
from hy_memory.config import MemoryConfig
from hy_memory.models.memory import MemoryLayer, MemoryNode, MemoryStatus


class DeterministicEmbeddingService:
    """Model boundary only: every query has the same known unit vector."""

    async def embed(self, text, use_cache=True):
        return [1.0] + [0.0] * 31

    async def embed_with_meta(self, text, use_cache=True):
        return await self.embed(text, use_cache), {}

    async def embed_batch(self, texts, use_cache=True):
        return [await self.embed(text, use_cache) for text in texts]

    async def embed_queued(self, text, use_cache=True):
        return await self.embed(text, use_cache)

    async def embed_queued_with_meta(self, text, use_cache=True):
        return await self.embed_with_meta(text, use_cache)

    def get_embedding_dims(self):
        return 32

    async def close(self):
        pass


def _node(node_id, content, *, dense_score=0.0, **fields):
    defaults = {
        "node_id": node_id,
        "user_id": "u1",
        "agent_id": "a1",
        "session_id": "s1",
        "source": "allowed",
        "layer": MemoryLayer.L2_FACT,
        "content": content,
        "embedding": [dense_score, math.sqrt(1.0 - dense_score ** 2)] + [0.0] * 30,
    }
    defaults.update(fields)
    return MemoryNode(**defaults)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MEMORY_ENABLE_GRAPH", "false")
    monkeypatch.setenv("MEMORY_SEARCH_DEDUP_ENABLED", "false")
    monkeypatch.setenv("MEMORY_PIPELINE_LOG_ENABLED", "false")
    monkeypatch.setenv("MEMORY_PIPELINE_TRACE_ENABLED", "false")
    config = MemoryConfig.from_dict({
        "vector_store": {
            "provider": "chroma",
            "collection_name": f"lex_public_{uuid.uuid4().hex[:10]}",
            "persist_directory": str(tmp_path / "chroma"),
            "embedding_dims": 32,
            "chroma_lexical_enabled": True,
        },
        "cache": {"backend": "sqlite", "db_path": str(tmp_path / "cache.db")},
        "history": {"enable": False},
        "coding": {"enable": False},
        "llm": {"api_key": "synthetic-test-key", "base_url": "http://127.0.0.1:1"},
    })
    sdk = HyMemoryClient(config, mode="pro", embedding_service=DeterministicEmbeddingService())
    try:
        yield sdk
    finally:
        sdk.close()


def _write(client, nodes):
    return client._loop_thread.run(client._vector_store.upsert_batch(nodes))


def _normal_ids(result):
    return [memory["memory_id"] for memory in result["memories"]["normal"]]


@pytest.mark.parametrize("query", ["ECONNRESET", "src/network/reconnect.py", "C++"])
def test_real_lexical_hit_outside_dense_top30_reaches_public_search(client, query):
    target = _node("lexical-target", f"Investigate {query} before restarting the process.")
    # If raw symbols are lost, C++ becomes a common, weak ordinary "c" hit.
    prefix = "C " if query == "C++" else ""
    distractors = [
        _node(f"dense-{number:02d}", f"{prefix}Unrelated training note number {number}", dense_score=0.95)
        for number in range(35)
    ]
    _write(client, distractors + [target])
    store = client._vector_store
    dense = client._loop_thread.run(store.search(
        [1.0] + [0.0] * 31, user_id="u1", agent_ids=["a1"], limit=30,
        layers=[MemoryLayer.L2_FACT],
    ))
    assert len(dense) == 30
    assert target.node_id not in {hit["node_id"] for hit in dense}

    # Both searches use real Chroma. The reader choice is the only change.
    legacy = client.search(query, scene="chat", reader="legacy", user_ids=["u1"], agent_ids=["a1"])
    hybrid = client.search(query, scene="chat", reader="hybrid", user_ids=["u1"], agent_ids=["a1"])
    assert target.node_id not in _normal_ids(legacy)
    assert target.node_id in _normal_ids(hybrid)
    returned = next(memory for memory in hybrid["memories"]["normal"] if memory["memory_id"] == target.node_id)
    assert returned["score"] >= 0.4

    strict = client.search(
        query, scene="chat", reader="hybrid", user_ids=["u1"], agent_ids=["a1"], min_score=0.99,
    )
    assert target.node_id not in _normal_ids(strict)


def test_public_lexical_search_keeps_source_user_session_and_shadow_scope(client):
    _write(client, [
        _node("visible", "ECONNRESET reconnect the socket"),
        _node("wrong-user", "ECONNRESET reconnect the socket", user_id="u2"),
        _node("wrong-session", "ECONNRESET reconnect the socket", session_id="s2"),
        _node("wrong-agent", "ECONNRESET reconnect the socket", agent_id="a2"),
        _node("excluded-source", "ECONNRESET reconnect the socket", source="blocked"),
        _node("shadow", "ECONNRESET reconnect the socket", status=MemoryStatus.SHADOW),
    ])
    result = client.search(
        "ECONNRESET", scene="chat", reader="hybrid", user_ids=["u1"],
        agent_ids=["a1"], session_ids=["s1"], include_sources=["allowed", "blocked"],
        exclude_sources=["blocked"],
    )
    assert _normal_ids(result) == ["visible"]


@pytest.mark.parametrize("path", ["src/foo.py", "src/配置/foo.py", "项目/模块.py"])
def test_public_hybrid_recalls_paths_touching_chinese(client, path):
    content = f"配置文件在{path}中" if path.startswith("src/") else f"{path}中有配置"
    _write(client, [_node("path-target", content)])
    result = client.search(
        path, scene="chat", reader="hybrid", user_ids=["u1"], agent_ids=["a1"],
    )
    assert _normal_ids(result) == ["path-target"]


def test_public_lexical_search_includes_the_second_requested_user(client):
    _write(client, [
        _node("second-user", "ECONNRESET belongs to the second requested user", user_id="u2"),
        _node("unrequested-user", "ECONNRESET belongs to another user", user_id="u3"),
    ])
    result = client.search(
        "ECONNRESET", scene="chat", reader="hybrid", user_ids=["u1", "u2"],
        agent_ids=["a1"],
    )
    assert _normal_ids(result) == ["second-user"]


def test_lexical_old_node_expands_to_head_before_event_time_cutoff(client):
    old_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    head_time = datetime(2026, 2, 1, tzinfo=timezone.utc)
    cutoff = datetime(2026, 1, 15, tzinfo=timezone.utc).timestamp()
    _write(client, [
        _node(
            "old", "ECONNRESET was initially diagnosed as a connection pool issue.",
            status=MemoryStatus.SUPERSEDED, is_latest=False, superseded_by=["head"],
            memory_at=old_time, gmt_created=old_time,
        ),
        _node(
            "head", "The current fix is to refresh stale credentials before opening a socket.",
            supersedes=["old"], memory_at=head_time, gmt_created=head_time,
        ),
    ])
    result = client.search(
        "ECONNRESET", scene="chat", reader="hybrid", user_ids=["u1"],
        agent_ids=["a1"], created_after=cutoff,
    )
    assert _normal_ids(result) == ["head"]
    returned = result["memories"]["normal"][0]
    assert returned["memory_at"] >= cutoff
    assert "refresh stale credentials" in returned["content"]

