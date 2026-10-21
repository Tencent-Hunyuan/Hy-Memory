from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hy_memory.client import HyMemoryClient
from hy_memory.config import MemoryConfig
from hy_memory.models.memory import MemoryLayer, MemoryNode


class VectorStore:
    _collection_name = "test"

    def __init__(self, node=None, *, delete_result=True):
        self.node = node
        self.delete_result = delete_result

    async def get_by_id(self, _memory_id):
        return self.node

    async def delete(self, _memory_id):
        result = self.node is not None and self.delete_result
        if result:
            self.node = None
        return result

    async def delete_retrieval_clauses(self, _memory_id):
        return None


class GraphStore:
    def __init__(self, node=None, *, delete_result=True, lookup_error=False):
        self.node = node
        self.delete_result = delete_result
        self.lookup_error = lookup_error

    async def get_node(self, _memory_id):
        if self.lookup_error:
            raise RuntimeError("graph unavailable")
        return self.node

    async def delete_node(self, _memory_id):
        if self.delete_result:
            self.node = None
        return self.delete_result


def _node():
    return MemoryNode(
        node_id="m1",
        user_id="u1",
        agent_id="a1",
        session_id="s1",
        content="body",
        layer=MemoryLayer.L2_FACT,
    )


def _client(vector, graph):
    client = HyMemoryClient.__new__(HyMemoryClient)
    client._vector_store = vector
    client._graph_store = graph
    client._history_store = None
    client._config = MemoryConfig.from_dict(
        {"vector_store": {"provider": "faiss", "collection_name": "test"}}
    )
    client._maybe_store_op_log = AsyncMock()
    client._cleanup_graph_evidence = AsyncMock(return_value=True)
    return client


@pytest.mark.asyncio
async def test_delete_succeeds_only_when_every_existing_store_is_deleted():
    client = _client(VectorStore(_node()), GraphStore(_node()))
    result = await HyMemoryClient.async_delete.__wrapped__(client, "m1")

    assert result["success"] is True
    assert result["deleted_count"] == 1
    assert result["vdb_deleted"] is True
    assert result["graph_deleted"] is True
    assert result["partial"] is False
    assert result["error_code"] is None


@pytest.mark.asyncio
async def test_graph_failure_is_reported_as_partial_not_success():
    client = _client(
        VectorStore(_node()),
        GraphStore(_node(), delete_result=False),
    )
    result = await HyMemoryClient.async_delete.__wrapped__(client, "m1")

    assert result["success"] is False
    assert result["deleted_count"] == 0
    assert result["vdb_deleted"] is True
    assert result["graph_deleted"] is False
    assert result["partial"] is True
    assert result["error_code"] == "delete_incomplete"


@pytest.mark.asyncio
async def test_graph_only_memory_can_be_physically_deleted():
    client = _client(VectorStore(None), GraphStore(_node()))
    result = await HyMemoryClient.async_delete.__wrapped__(client, "m1")

    assert result["success"] is True
    assert result["vdb_deleted"] is False
    assert result["graph_deleted"] is True
    assert result["deleted_count"] == 1


@pytest.mark.asyncio
async def test_graph_lookup_failure_never_collapses_to_not_found_success():
    client = _client(VectorStore(None), GraphStore(lookup_error=True))
    result = await HyMemoryClient.async_delete.__wrapped__(client, "m1")

    assert result["success"] is False
    assert result["partial"] is False
    assert result["error_code"] == "delete_incomplete"
