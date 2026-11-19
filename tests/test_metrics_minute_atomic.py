# -*- coding: utf-8 -*-
"""Regression: concurrent metric writes from multiple cache instances must
not lose counts or raise UNIQUE constraint failures.

store_metrics_minute did SELECT -> json merge in Python -> UPDATE (or plain
INSERT). The instance-level RLock does not protect two different SqliteCache
objects sharing one DB file, so both read the same stale JSON and overwrite
each other; the INSERT branch can also race into a UNIQUE violation.
"""

import asyncio
import os
import tempfile
import uuid
from types import SimpleNamespace

from hy_memory.data.cache_sqlite import SqliteCache


def _make_cache(db_path):
    return SqliteCache(SimpleNamespace(cache=SimpleNamespace(db_path=db_path, backend="sqlite")))


def _fresh_db():
    db = os.path.join(
        tempfile.gettempdir(), f"hy_metrics_atomic_{uuid.uuid4().hex[:12]}.db"
    )
    for suffix in ("", "-wal", "-shm"):
        path = db + suffix
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass
    return db


def test_two_instances_concurrent_writes_keep_all_counts():
    async def scenario():
        db = _fresh_db()
        c1, c2 = _make_cache(db), _make_cache(db)
        await c1.initialize()
        await c2.initialize()
        minute = "2026-11-19T12:00"
        n = 300

        async def worker(cache):
            for _ in range(n):
                await cache.store_metrics_minute(minute, {"sys1_completed": 1})

        await asyncio.gather(worker(c1), worker(c2))
        rows = await c1.load_metrics_range(minute, minute)
        total = sum(r.get("sys1_completed", 0) for r in rows)
        await c1.close()
        await c2.close()
        return total, 2 * n

    total, expected = asyncio.run(scenario())
    assert total == expected, f"lost {expected - total}/{expected} metric increments"
