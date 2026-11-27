"""Capsule ordering must survive a bank that holds naive and aware event times.

Locally written memories carry a naive ``memory_at`` (``datetime.now()``) while
memories read back from an ISO payload that spells an offset are aware.  The
capsule builders ordered those values directly, so ``sort`` / ``min`` / ``max``
raised ``TypeError: can't compare offset-naive and offset-aware datetimes`` and
the whole episode finalize aborted — the session kept its raw rows and never got
a capsule.
"""

import asyncio
from datetime import datetime, timezone

from hy_memory.agent.episode_summary import (
    _earliest_memory_at,
    _latest_memory_at,
    _raw_order_key,
    summarize_closed_episode,
)
from hy_memory.models.memory import MemoryLayer, MemoryNode


def node(content, *, at=None, created=None, node_id="n"):
    return MemoryNode(
        node_id=node_id,
        user_id="u",
        agent_id="a",
        session_id="s",
        episode_id="ep1",
        layer=MemoryLayer.L2_FACT,
        content=content,
        owner="user",
        memory_at=at,
        gmt_created=created,
    )


AWARE = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
NAIVE_LATE = datetime(2026, 9, 10, 23, 0)
NAIVE_EARLY = datetime(2026, 9, 10, 1, 0)


def test_the_raw_order_key_orders_naive_and_aware_times_together():
    nodes = [
        node("late", at=NAIVE_LATE, node_id="late"),
        node("aware", at=AWARE, node_id="aware"),
        node("early", at=NAIVE_EARLY, node_id="early"),
    ]

    ordered = [item.node_id for item in sorted(nodes, key=_raw_order_key)]

    expected = [
        item.node_id
        for item in sorted(nodes, key=lambda item: item.memory_at.timestamp())
    ]
    assert ordered == expected


def test_the_raw_order_key_falls_back_to_creation_time():
    nodes = [
        node("no-event-time", created=NAIVE_EARLY, node_id="created"),
        node("event-time", at=AWARE, node_id="event"),
    ]

    ordered = [item.node_id for item in sorted(nodes, key=_raw_order_key)]

    assert ordered == ["created", "event"]


def test_earliest_and_latest_ignore_naive_aware_mixing():
    nodes = [
        node("late", at=NAIVE_LATE),
        node("aware", at=AWARE),
        node("early", at=NAIVE_EARLY),
    ]

    assert _earliest_memory_at(nodes) is NAIVE_EARLY
    assert _latest_memory_at(nodes) is NAIVE_LATE
    assert _earliest_memory_at([]) is None
    assert _latest_memory_at([]) is None


class _FakeLLM:
    async def complete(self, prompt, **kw):
        return type("R", (), {"content": "用户这次讨论了周末的安排。"})()


class _FakeEmbed:
    async def embed_queued(self, text):
        return [0.1] * 8


class _FakeVDB:
    def __init__(self, nodes):
        self._nodes = nodes
        self.upserted = []

    async def list_by_episode(self, episode_ids, **kw):
        want = set(episode_ids)
        return [n for n in self._nodes if (n.episode_id or "") in want]

    async def upsert(self, node):
        self.upserted.append(node)
        return node.node_id


def test_a_capsule_keeps_the_latest_event_time_in_a_mixed_bank():
    nodes = [
        node("a", at=NAIVE_EARLY),
        node("b", at=AWARE),
        node("c", at=NAIVE_LATE),
    ]
    vdb = _FakeVDB(nodes)

    asyncio.run(summarize_closed_episode(
        vector_store=vdb,
        embed_service=_FakeEmbed(),
        llm_provider=_FakeLLM(),
        episode_id="ep1",
        user_id="u",
    ))

    assert vdb.upserted, "the capsule must be stored, not lost to a TypeError"
    assert vdb.upserted[0].memory_at is NAIVE_LATE
