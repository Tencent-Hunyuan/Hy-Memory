from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.models.memory import MemoryLayer


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

    async def get_by_id(self, node_id):
        return self.nodes.get(node_id)

    async def upsert(self, node):
        self.upserts += 1
        self.nodes[node.node_id] = node
        return node.node_id

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
