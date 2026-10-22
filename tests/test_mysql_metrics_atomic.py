# -*- coding: utf-8 -*-
"""MySQL minute metrics must merge under one row lock."""

import asyncio
import json

import pytest

pytest.importorskip("aiomysql")

from hy_memory.config import MemoryConfig  # noqa: E402
from hy_memory.data.cache_mysql import MysqlCache  # noqa: E402


class FakeCursor:
    def __init__(self, row):
        self.row = row
        self.executed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def execute(self, sql, args=None):
        self.executed.append((sql, args))

    async def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, row):
        self.cursor_obj = FakeCursor(row)
        self.begun = False
        self.committed = False
        self.rolled_back = False

    async def begin(self):
        self.begun = True

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    def cursor(self, *args, **kwargs):
        return self.cursor_obj


class _Acquire:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *args):
        return None


class FakePool:
    def __init__(self, connection):
        self.connection = connection

    def acquire(self):
        return _Acquire(self.connection)


def _cache_with(row):
    cache = MysqlCache(MemoryConfig())
    connection = FakeConnection(row)
    cache._pool = FakePool(connection)
    return cache, connection


def test_metrics_insert_takes_the_row_lock_and_commits():
    cache, connection = _cache_with(None)

    asyncio.run(
        cache.store_metrics_minute("2026-01-01T00:00", {"sys1_started": 2})
    )

    statements = [sql for sql, _ in connection.cursor_obj.executed]
    assert any("FOR UPDATE" in sql for sql in statements)
    assert any(sql.startswith("INSERT") for sql in statements)
    assert connection.begun and connection.committed
    assert not connection.rolled_back


def test_metrics_update_merges_existing_buckets():
    old = {"sys1_started": 1, "sys1_timing_sums": {"a": 1}}
    cache, connection = _cache_with({"data": json.dumps(old)})

    asyncio.run(
        cache.store_metrics_minute(
            "2026-01-01T00:00",
            {"sys1_started": 2, "sys1_timing_sums": {"a": 3}},
        )
    )

    update = next(
        args for sql, args in connection.cursor_obj.executed
        if sql.startswith("UPDATE")
    )
    merged = json.loads(update[0])
    assert merged["sys1_started"] == 3
    assert merged["sys1_timing_sums"]["a"] == 4
    assert connection.committed
