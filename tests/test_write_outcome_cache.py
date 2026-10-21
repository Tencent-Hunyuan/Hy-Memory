from __future__ import annotations

import pytest

from hy_memory.config import MemoryConfig
from hy_memory.data.cache_sqlite import SqliteCache


@pytest.fixture
async def cache(tmp_path):
    config = MemoryConfig.from_dict(
        {"cache": {"backend": "sqlite", "db_path": str(tmp_path / "cache.sqlite3")}}
    )
    value = SqliteCache(config)
    await value.initialize()
    yield value
    await value.close()


@pytest.mark.asyncio
async def test_claim_is_atomic_and_returns_existing_scope(cache):
    claimed = await cache.claim_write_outcome("r1", "u1", "a1", "s1", "add")
    assert claimed is None

    existing = await cache.claim_write_outcome("r1", "u1", "a1", "s1", "add")
    assert existing["status"] == "running"
    assert existing["user_id"] == "u1"
    assert existing["operation"] == "add"


@pytest.mark.asyncio
async def test_terminal_result_round_trips(cache):
    await cache.claim_write_outcome("r2", "u1", "a1", "s1", "add_extracted")
    assert await cache.finish_write_outcome(
        "r2",
        "succeeded",
        {"success": True, "memory_ids": ["m1", "m2"]},
    )

    outcome = await cache.get_write_outcome("r2")
    assert outcome["status"] == "succeeded"
    assert outcome["result"] == {"success": True, "memory_ids": ["m1", "m2"]}


@pytest.mark.asyncio
async def test_missing_outcome_is_distinct_from_running(cache):
    assert await cache.get_write_outcome("missing") is None
