"""Native asynchronous MySQL history; database provisioning belongs to operators."""

import asyncio
import json
from typing import Any, Dict, List, Optional

from .history_store import HistoryStore, _now_iso

# Indexed equality prefilters plus bytewise CAST refinements preserve trailing
# spaces on PAD SPACE collations without wrapping the indexable predicate. Text columns deliberately preserve the SQLite row representation.
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memory_history (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    memory_id TEXT NOT NULL,
    isolation_key TEXT NOT NULL,
    event TEXT NOT NULL,
    old_memory LONGTEXT NULL,
    new_memory LONGTEXT NULL,
    old_status TEXT NULL,
    new_status TEXT NULL,
    change_reason TEXT NULL,
    layer TEXT NULL,
    actor_id TEXT NULL,
    role TEXT NULL,
    extra LONGTEXT NULL,
    created_at VARCHAR(64) NOT NULL,
    updated_at VARCHAR(64) NOT NULL,
    INDEX idx_history_memory_id (memory_id(191)),
    INDEX idx_history_isolation_key (isolation_key(191)),
    INDEX idx_history_created_at (created_at, id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin
"""


class MysqlHistoryStore(HistoryStore):
    """HistoryStore's record methods backed by aiomysql, with bounded I/O.

    get_history(memory_id) is intentionally unscoped, not an authorization
    boundary. Callers must authorize access before using it.
    """

    CONNECT_TIMEOUT = 10.0
    READ_TIMEOUT = 15.0
    WRITE_TIMEOUT = 15.0
    INIT_TIMEOUT = 30.0
    CLOSE_TIMEOUT = 5.0
    MAX_POOL_SIZE = 32

    def __init__(self, config):
        self._cache = config.cache
        self._pool = None
        self._driver = None
        self._lifecycle_lock = asyncio.Lock()
        size = self._cache.mysql_pool_size
        if isinstance(size, bool) or not isinstance(size, int) or not 1 <= size <= self.MAX_POOL_SIZE:
            raise ValueError("History MySQL pool size must be between 1 and 32")

    async def initialize(self) -> None:
        """Create only the history table inside an already provisioned database."""
        async with self._lifecycle_lock:
            await self._close_pool()
            # Optional dependency: SQLite users never import aiomysql.
            import aiomysql

            self._driver = aiomysql
            await asyncio.wait_for(self._initialize(), timeout=self.INIT_TIMEOUT)

    async def _initialize(self):
        cfg = self._cache
        pool = None
        try:
            pool = await asyncio.wait_for(
                self._driver.create_pool(
                    host=cfg.mysql_host, port=cfg.mysql_port,
                    user=cfg.mysql_user, password=cfg.mysql_password,
                    db=cfg.mysql_database, minsize=0, maxsize=cfg.mysql_pool_size,
                    pool_recycle=cfg.mysql_pool_recycle, charset="utf8mb4",
                    autocommit=True, connect_timeout=self.CONNECT_TIMEOUT,
                ), timeout=self.CONNECT_TIMEOUT,
            )
            self._pool = pool
            await self._execute(_CREATE_TABLE_SQL, (), "write")
        except BaseException:
            self._pool = None
            if pool is not None:
                pool.terminate()
                await asyncio.wait_for(pool.wait_closed(), self.CLOSE_TIMEOUT)
            raise

    async def _execute(self, sql, params, mode):
        pool = self._pool
        if pool is None:
            raise RuntimeError("History MySQL pool is not initialized")

        async def operation():
            async with pool.acquire() as connection:
                async with connection.cursor(self._driver.DictCursor) as cursor:
                    await cursor.execute(sql, params)
                    if mode == "rows":
                        return [dict(row) for row in await cursor.fetchall()]
                    if mode == "count":
                        return (await cursor.fetchone())["n"]
                    return cursor.lastrowid

        budget = self.READ_TIMEOUT if mode in ("rows", "count") else self.WRITE_TIMEOUT
        return await asyncio.wait_for(operation(), timeout=budget)

    async def _insert(
        self, memory_id: str, isolation_key: str, event: str,
        old_memory: str = None, new_memory: str = None,
        old_status: str = None, new_status: str = None,
        change_reason: str = None, layer: str = None,
        actor_id: str = "", role: str = "", extra: Optional[Dict[str, Any]] = None,
    ) -> int:
        if self._pool is None:
            return -1
        now = _now_iso()
        return int(await self._execute(
            "INSERT INTO memory_history "
            "(memory_id, isolation_key, event, old_memory, new_memory, old_status, "
            "new_status, change_reason, layer, actor_id, role, extra, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (memory_id, isolation_key, event, old_memory, new_memory, old_status,
             new_status, change_reason, layer, actor_id, role,
             json.dumps(extra or {}, ensure_ascii=False), now, now), "write",
        ))

    async def get_history(self, memory_id: str) -> List[Dict[str, Any]]:
        """Return unscoped memory history; callers own authorization."""
        if self._pool is None:
            return []
        return await self._execute(
            "SELECT * FROM memory_history WHERE memory_id = %s AND CAST(memory_id AS BINARY) = CAST(%s AS BINARY) "
            "ORDER BY created_at ASC, id ASC", (memory_id, memory_id), "rows",
        )

    async def get_recent(self, isolation_key: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Return exact isolation matches, newest first (negative limit: all)."""
        if self._pool is None:
            return []
        # SQLite treats negative LIMIT as unlimited; MySQL rejects it.
        sql = ("SELECT * FROM memory_history WHERE isolation_key = %s AND CAST(isolation_key AS BINARY) = CAST(%s AS BINARY) "
               "ORDER BY created_at DESC, id DESC")
        params = (isolation_key, isolation_key)
        if limit >= 0:
            sql += " LIMIT %s"
            params += (limit,)
        return await self._execute(sql, params, "rows")

    async def count(self, isolation_key: str = "") -> int:
        """An empty key counts all rows, matching SQLite's existing contract."""
        if self._pool is None:
            return 0
        sql = "SELECT COUNT(*) AS n FROM memory_history"
        params = ()
        if isolation_key:
            sql += " WHERE isolation_key = %s AND CAST(isolation_key AS BINARY) = CAST(%s AS BINARY)"
            params = (isolation_key, isolation_key)
        return int(await self._execute(sql, params, "count"))

    async def _close_pool(self):
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()
            try:
                await asyncio.wait_for(pool.wait_closed(), self.CLOSE_TIMEOUT)
            except BaseException:
                pool.terminate()
                raise

    async def close(self) -> None:
        """Release the pool, including checked out connections on timeout."""
        async with self._lifecycle_lock:
            await self._close_pool()
