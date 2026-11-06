# -*- coding: utf-8 -*-
"""Session-only delete_all must cover every agent, not just the default."""

import asyncio
from types import SimpleNamespace

from hy_memory.client import HyMemoryClient


class FakeVectorStore:
    def __init__(self, agents):
        self.agents = agents
        self.calls = []
        self.list_kwargs = None

    async def list_by_user(self, **kwargs):
        self.list_kwargs = kwargs
        return [SimpleNamespace(agent_id=agent) for agent in self.agents]

    async def delete_by_metadata(self, user_id, agent_id=None, session_id=None):
        self.calls.append((agent_id, session_id))
        return 1

    async def delete_retrieval_clauses_by_metadata(self, **kwargs):
        return 0


def _client(vector_store):
    graph_calls = []

    async def purge_graph(user_id, agent_id=None, session_id=None):
        graph_calls.append((agent_id, session_id))
        return 0, 0.0, False

    async def noop_op_log(**kwargs):
        return None

    ns = SimpleNamespace(
        _vector_store=vector_store,
        _purge_graph_data=purge_graph,
        _maybe_store_op_log=noop_op_log,
        _mode="pro",
    )
    ns._list_user_agent_ids = HyMemoryClient._list_user_agent_ids.__get__(
        ns, type(ns),
    )
    return ns, graph_calls


def test_session_only_delete_covers_every_agent():
    vector_store = FakeVectorStore(["a2", "a1"])
    client, graph_calls = _client(vector_store)

    asyncio.run(
        HyMemoryClient.async_delete_all.__wrapped__(
            client, user_id="u1", session_ids=["s1"],
        )
    )

    assert vector_store.calls == [("a1", "s1"), ("a2", "s1")]
    assert graph_calls == [("a1", "s1"), ("a2", "s1")]
    assert vector_store.list_kwargs["allow_raw_layer"] is True


def test_session_only_delete_falls_back_to_all_agents_when_none_are_known():
    vector_store = FakeVectorStore([])
    client, graph_calls = _client(vector_store)

    asyncio.run(
        HyMemoryClient.async_delete_all.__wrapped__(
            client, user_id="u1", session_ids=["s1"],
        )
    )

    assert vector_store.calls == [(None, "s1")]
    assert graph_calls == [(None, "s1")]
