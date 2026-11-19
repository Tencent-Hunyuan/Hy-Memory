# -*- coding: utf-8 -*-
"""Regression: concurrent episode resolution must not lose memory_count or
fragment a session into many episode_ids.

_resolve_episode_id used a check-then-act sequence: get_episode_state()
(SELECT), increment locally, then upsert_episode_state() (overwrite). The
per-call lock was released between the two, so N concurrent writes all read
the same stale state and overwrote each other. Under 20 concurrent writes the
count ended at 2 instead of 20 and the session fragmented into many episodes.

The tests wrap async bodies with asyncio.run() so they need no pytest-asyncio.
"""

import asyncio
import os
import tempfile
import uuid
from types import SimpleNamespace

from hy_memory.data.cache_sqlite import SqliteCache
from hy_memory.pipelines.writer import MemoryWriter


class StubWriter:
    """Exposes the real _resolve_episode_id with no LLM/summary dependencies."""

    _resolve_episode_id = MemoryWriter._resolve_episode_id

    def __init__(self, cache, config):
        self._cache = cache
        self.config = config
        self._scheduled = []

    def _schedule_episode_summary(self, request, episode_id):
        self._scheduled.append(episode_id)

    async def _log_episode(self, request, **kwargs):
        pass


def _make_config(db_path):
    return SimpleNamespace(
        cache=SimpleNamespace(db_path=db_path, backend="sqlite"),
        recall=SimpleNamespace(
            episode_gap_seconds=1800.0,
            episode_max_memories=10_000_000,
        ),
    )


def _fresh_db():
    # Unique name per call so the two tests cannot share WAL/shm residue.
    db = os.path.join(
        tempfile.gettempdir(), f"hy_episode_atomic_{uuid.uuid4().hex[:12]}.db"
    )
    for suffix in ("", "-wal", "-shm"):
        path = db + suffix
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    return db


def test_20_concurrent_writes_keep_one_episode_and_full_count():
    async def scenario():
        db = _fresh_db()
        config = _make_config(db)
        cache = SqliteCache(config)
        await cache.initialize()
        try:
            writer = StubWriter(cache, config)
            req = SimpleNamespace(
                user_id="u1", agent_id="a1", session_id="s1", memory_at=None
            )
            n = 20
            episode_ids = await asyncio.gather(
                *[writer._resolve_episode_id(req, 1) for _ in range(n)]
            )
            state = await cache.get_episode_state("u1::a1::s1")
            return episode_ids, state, n
        finally:
            await cache.close()

    episode_ids, state, n = asyncio.run(scenario())
    assert len(set(episode_ids)) == 1, (
        f"session fragmented into {len(set(episode_ids))} episodes"
    )
    assert state["memory_count"] == n, (
        f"memory_count={state['memory_count']}, expected {n}"
    )


def test_count_rolls_over_at_cap_without_exceeding_it():
    async def scenario():
        db = _fresh_db()
        config = _make_config(db)
        config.recall.episode_max_memories = 5
        cache = SqliteCache(config)
        await cache.initialize()
        try:
            writer = StubWriter(cache, config)
            req = SimpleNamespace(
                user_id="u2", agent_id="a1", session_id="s1", memory_at=None
            )
            ids = []
            for _ in range(6):
                ids.append(await writer._resolve_episode_id(req, 1))
            state = await cache.get_episode_state("u2::a1::s1")
            return ids, state, writer._scheduled
        finally:
            await cache.close()

    ids, state, scheduled = asyncio.run(scenario())
    assert len(set(ids)) == 2, "cap should trigger exactly one rollover"
    assert state["memory_count"] == 1, "new episode starts at 1"
    assert len(scheduled) == 1, "closed episode summarized once"
