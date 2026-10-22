from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.models.memory import (
    MemoryLayer,
    MemoryNode,
    MemoryStatus,
    SourceType,
)


class RawStore:
    def __init__(self, nodes: list[MemoryNode]):
        self.nodes = nodes

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
        for node in self.nodes:
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


def raw_node(raw_id: str, session_id: str, source: str) -> MemoryNode:
    return MemoryNode(
        node_id=raw_id,
        user_id="u1",
        agent_id="a1",
        session_id=session_id,
        source=source,
        layer=MemoryLayer.L1_RAW,
        content=f"content {raw_id}",
        source_type=SourceType.EXPLICIT,
        status=MemoryStatus.ACTIVE,
        is_latest=True,
        embedding=[0.1, 0.2],
    )


def test_migration_only_methods_are_not_public_client_api():
    for name in (
        "get_raw",
        "async_get_raw",
        "list_raw",
        "async_list_raw",
        "import_raw",
        "async_import_raw",
        "import_memory_exact",
        "async_import_memory_exact",
    ):
        assert not hasattr(HyMemoryClient, name)
    assert "include_raw" not in inspect.signature(HyMemoryClient.get).parameters


@pytest.mark.asyncio
async def test_existing_search_contract_can_explicitly_search_raw():
    client = HyMemoryClient.__new__(HyMemoryClient)
    client._vector_store = RawStore(
        [
            raw_node("raw-1", "s1", "cursor"),
            raw_node("raw-2", "s2", "claude"),
        ]
    )
    client._embed_service = AsyncMock()
    client._embed_service.embed.return_value = [0.1, 0.2]

    result = await HyMemoryClient._search_explicit_raw_layer.__wrapped__(
        client,
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
