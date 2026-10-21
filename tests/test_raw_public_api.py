from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.config import MemoryConfig
from hy_memory.models.memory import (
    MemoryLayer,
    MemoryNode,
    MemoryStatus,
    SourceType,
)
from hy_memory.pipelines.base import WriteRequest
from hy_memory.pipelines.writer import MemoryWriter


class OutcomeCache:
    def __init__(self):
        self.rows = {}

    async def claim_write_outcome(
        self, request_id, user_id, agent_id, session_id, operation
    ):
        if request_id in self.rows:
            return self.rows[request_id]
        self.rows[request_id] = {
            "request_id": request_id,
            "user_id": user_id,
            "agent_id": agent_id,
            "session_id": session_id,
            "operation": operation,
            "status": "running",
            "result": {},
        }
        return None

    async def finish_write_outcome(self, request_id, status, result):
        self.rows[request_id].update(status=status, result=dict(result))
        return True

    async def get_write_outcome(self, request_id):
        return self.rows.get(request_id)


class RawStore:
    def __init__(self):
        self.nodes = {}
        self.upserts = 0
        self.supports_fulltext = False

    async def get_by_id(self, node_id):
        return self.nodes.get(node_id)

    async def initialize(self):
        return None

    async def upsert(self, node):
        self.upserts += 1
        self.nodes[node.node_id] = node
        return node.node_id

    async def update_payload(self, node_id, payload):
        node = self.nodes[node_id]
        for key, value in payload.items():
            setattr(node, key, value)

    async def list_by_user(
        self,
        *,
        user_id,
        agent_id=None,
        include_sources=None,
        **_kwargs,
    ):
        return [
            node
            for node in self.nodes.values()
            if node.user_id == user_id
            and (agent_id is None or node.agent_id == agent_id)
            and (not include_sources or node.source in include_sources)
        ]

    async def search(
        self,
        *,
        user_ids,
        agent_ids,
        include_sources,
        exclude_sources,
        layers,
        limit,
        **_kwargs,
    ):
        rows = []
        for node in self.nodes.values():
            if node.user_id not in user_ids:
                continue
            if agent_ids and node.agent_id not in agent_ids:
                continue
            if include_sources and node.source not in include_sources:
                continue
            if exclude_sources and node.source in exclude_sources:
                continue
            if node.layer not in layers:
                continue
            rows.append({"node": node, "score": 0.9})
        return rows[:limit]


def _client():
    client = HyMemoryClient.__new__(HyMemoryClient)
    client._cache = OutcomeCache()
    client._vector_store = RawStore()
    client._embed_service = AsyncMock()
    client._embed_service.embed.return_value = [0.1, 0.2]
    return client


@pytest.mark.asyncio
async def test_import_raw_preserves_identity_without_extraction():
    client = _client()
    result = await HyMemoryClient.async_import_raw.__wrapped__(
        client,
        "verbatim",
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        source="cursor",
        raw_id="raw-1",
        request_id="request-1",
        metadata={"role": "user", "source_order": 7},
    )

    assert result == {
        "success": True,
        "raw_id": "raw-1",
        "request_id": "request-1",
    }
    node = client._vector_store.nodes["raw-1"]
    assert node.layer == MemoryLayer.L1_RAW
    assert node.content == "verbatim"
    assert node.custom["role"] == "user"
    assert node.custom["source_order"] == 7
    assert node.custom["input_kind"] == "raw_exact_import"


@pytest.mark.asyncio
async def test_import_raw_request_replay_does_not_write_twice():
    client = _client()
    first = await HyMemoryClient.async_import_raw.__wrapped__(
        client, "same", user_id="u1", raw_id="raw-1", request_id="request-1"
    )
    second = await HyMemoryClient.async_import_raw.__wrapped__(
        client, "same", user_id="u1", raw_id="raw-1", request_id="request-1"
    )

    assert second == {**first, "replayed": True}
    assert client._vector_store.upserts == 1


@pytest.mark.asyncio
async def test_process_raw_is_idempotent_and_does_not_insert_raw_again():
    client = _client()
    for index, role in enumerate(("user", "assistant"), start=1):
        await HyMemoryClient.async_import_raw.__wrapped__(
            client,
            f"turn-{index}",
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            source="cursor",
            raw_id=f"raw-{index}",
            request_id=f"import-{index}",
            metadata={"role": role, "source_order": index},
        )
    client._async_add_impl = AsyncMock(
        return_value={"success": True, "memory_ids": ["memory-1"]}
    )

    first = await HyMemoryClient.async_process_raw.__wrapped__(
        client,
        ["raw-1", "raw-2"],
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        source="cursor",
        request_id="process-1",
    )
    second = await HyMemoryClient.async_process_raw.__wrapped__(
        client,
        ["raw-1", "raw-2"],
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        source="cursor",
        request_id="process-1",
    )

    assert first["raw_ids"] == ["raw-1", "raw-2"]
    assert first["memory_ids"] == ["memory-1"]
    assert second == {**first, "replayed": True}
    assert client._vector_store.upserts == 2
    client._async_add_impl.assert_awaited_once()
    assert client._async_add_impl.await_args.kwargs["_existing_raw_ids"] == [
        "raw-1",
        "raw-2",
    ]


@pytest.mark.asyncio
async def test_writer_reuses_existing_raw_without_embedding_or_upsert():
    store = RawStore()
    raw = MemoryNode(
        node_id="raw-1",
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        source="cursor",
        layer=MemoryLayer.L1_RAW,
        content="verbatim",
        source_type=SourceType.EXPLICIT,
        status=MemoryStatus.ACTIVE,
        is_latest=True,
        embedding=[0.1, 0.2],
    )
    store.nodes[raw.node_id] = raw
    embed = AsyncMock()
    writer = MemoryWriter(
        MemoryConfig(),
        embed_service=embed,
        vector_store=store,
        cache=None,
    )

    response = await writer.write(
        WriteRequest(
            content="[user]: verbatim",
            user_id="u1",
            agent_id="a1",
            session_id="s1",
            source="cursor",
            request_id="process-1",
            existing_raw_ids=["raw-1"],
            extra={
                "agent_mode": "disabled",
            },
        )
    )

    assert response.success is True
    assert response.memory_id == "raw-1"
    assert store.upserts == 0
    embed.embed_queued_with_meta.assert_not_awaited()


@pytest.mark.asyncio
async def test_existing_raw_id_must_match_exact_content_and_owner():
    client = _client()
    await HyMemoryClient.async_import_raw.__wrapped__(
        client, "first", user_id="u1", raw_id="raw-1", request_id="request-1"
    )

    with pytest.raises(ValueError, match="different source content"):
        await HyMemoryClient.async_import_raw.__wrapped__(
            client, "changed", user_id="u1", raw_id="raw-1", request_id="request-2"
        )


@pytest.mark.asyncio
async def test_raw_get_and_list_use_explicit_api():
    client = _client()
    for raw_id, session_id, source in (
        ("raw-1", "s1", "cursor"),
        ("raw-2", "s2", "claude"),
    ):
        await HyMemoryClient.async_import_raw.__wrapped__(
            client,
            raw_id,
            user_id="u1",
            session_id=session_id,
            source=source,
            raw_id=raw_id,
            request_id=f"request-{raw_id}",
        )

    item = await HyMemoryClient.async_get_raw.__wrapped__(client, "raw-1")
    assert item["raw_id"] == "raw-1"
    assert item["content"] == "raw-1"

    page = await HyMemoryClient.async_list_raw.__wrapped__(
        client,
        user_id="u1",
        source="claude",
        limit=10,
        offset=0,
    )
    assert page["total"] == 1
    assert [item["raw_id"] for item in page["raw"]] == ["raw-2"]


@pytest.mark.asyncio
async def test_exact_memory_import_preserves_id_lifecycle_and_chain():
    client = _client()
    result = await HyMemoryClient.async_import_memory_exact.__wrapped__(
        client,
        "older preference",
        memory_id="memory-1",
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        source="cursor",
        layer="l2_fact",
        status="superseded",
        owner="user",
        tags=["preference"],
        superseded_by=["memory-2"],
        is_latest=False,
        request_id="request-memory-1",
    )

    assert result["memory_ids"] == ["memory-1"]
    node = client._vector_store.nodes["memory-1"]
    assert node.content == "older preference"
    assert node.layer == MemoryLayer.L2_FACT
    assert node.status == MemoryStatus.SUPERSEDED
    assert node.superseded_by == ["memory-2"]
    assert node.is_latest is False
    assert node.custom["input_kind"] == "memory_exact_import"


@pytest.mark.asyncio
async def test_exact_memory_import_refuses_same_id_with_different_body():
    client = _client()
    await HyMemoryClient.async_import_memory_exact.__wrapped__(
        client,
        "first",
        memory_id="memory-1",
        user_id="u1",
        request_id="request-memory-1",
    )

    with pytest.raises(ValueError, match="different memory content"):
        await HyMemoryClient.async_import_memory_exact.__wrapped__(
            client,
            "changed",
            memory_id="memory-1",
            user_id="u1",
            request_id="request-memory-2",
        )


@pytest.mark.asyncio
async def test_exact_memory_import_rejects_raw_layer():
    client = _client()
    with pytest.raises(ValueError, match="use import_raw"):
        await HyMemoryClient.async_import_memory_exact.__wrapped__(
            client,
            "raw",
            memory_id="memory-1",
            user_id="u1",
            layer="l1_raw",
            request_id="request-memory-1",
        )


@pytest.mark.asyncio
async def test_existing_search_contract_can_explicitly_search_raw_with_source_filter():
    client = _client()
    for raw_id, session_id, source in (
        ("raw-1", "s1", "cursor"),
        ("raw-2", "s2", "claude"),
    ):
        await HyMemoryClient.async_import_raw.__wrapped__(
            client,
            f"content {raw_id}",
            user_id="u1",
            agent_id="a1",
            session_id=session_id,
            source=source,
            raw_id=raw_id,
            request_id=f"request-{raw_id}",
        )

    result = await client._search_explicit_raw_layer(
        "content",
        user_ids=["u1"],
        agent_ids=["a1"],
        session_ids=["s2"],
        include_sources=["claude"],
        exclude_sources=["cursor"],
        limit=5,
        min_score=0.0,
        created_after=None,
        request_id="search-1",
        t0=0.0,
        query_embedding=[0.1, 0.2],
    )

    assert [item["memory_id"] for item in result["memories"]["normal"]] == [
        "raw-2"
    ]
    assert result["memories"]["normal"][0]["layer"] == "l1_raw"
    assert result["memories"]["normal"][0]["source"] == "claude"
