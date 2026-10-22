# -*- coding: utf-8 -*-
"""
History Store - SQLite 审计追踪

基于 Python 标准库 sqlite3（零额外依赖），通过 _run_in_sqlite_pool() 包装为异步。
记录所有内存操作（ADD/UPDATE/DELETE/SEARCH）的完整历史，用于审计追踪和版本控制。

设计决策:
- 独立于 rdb.py 抽象，history 只需 SQLite，不需要 MySQL/PG 切换
- 通过 _run_in_sqlite_pool() 包装同步 sqlite3 调用，避免阻塞事件循环
- 所有写入操作都是 try-except 安全的，失败不影响主流程
"""

import functools
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional

from .cache_sqlite import _sqlite_executor
from ..utils.async_executor import run_in_executor_guarded

logger = logging.getLogger(__name__)


async def _run_in_sqlite_pool(func, *args, **kwargs):
    """在 SQLite 独立线程池中执行同步函数"""
    call = functools.partial(func, *args, **kwargs) if args or kwargs else func
    return await run_in_executor_guarded(_sqlite_executor, call)

# SQL: 创建表
_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memory_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    memory_id     TEXT NOT NULL,
    isolation_key TEXT NOT NULL,
    event         TEXT NOT NULL,
    old_memory    TEXT,
    new_memory    TEXT,
    old_status    TEXT,
    new_status    TEXT,
    change_reason TEXT,
    layer         TEXT,
    actor_id      TEXT DEFAULT '',
    role          TEXT DEFAULT '',
    extra         TEXT DEFAULT '{}',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
"""

# SQL: 创建索引
_CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_history_memory_id ON memory_history(memory_id);",
    "CREATE INDEX IF NOT EXISTS idx_history_isolation_key ON memory_history(isolation_key);",
    "CREATE INDEX IF NOT EXISTS idx_history_created_at ON memory_history(created_at);",
    "CREATE INDEX IF NOT EXISTS idx_history_event ON memory_history(event);",
]


def _now_iso() -> str:
    """返回当前 UTC 时间的 ISO 格式字符串"""
    return datetime.now(timezone.utc).isoformat()


def _escape_like(value: str) -> str:
    """Escape LIKE wildcards so a user id is matched literally."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class HistoryStore:
    """
    SQLite 历史存储 - 记录所有内存操作的审计日志。

    Usage:
        store = HistoryStore(config)
        await store.initialize()

        await store.record_add(memory_id="abc", content="...", layer="raw",
                               isolation_key="app:user")

        history = await store.get_history("abc")
        recent = await store.get_recent("app:user", limit=50)

        await store.close()
    """

    def __init__(self, config):
        """
        Args:
            config: MemoryConfig 实例，从 config.history 读取配置
        """
        self._db_path = config.history.db_path
        self._conn: Optional[sqlite3.Connection] = None
        # sqlite3.Connection.close() must never race with an execute() on the
        # same connection. The lock also serializes operations on this shared
        # connection; separate HistoryStore instances remain independent.
        self._conn_lock = threading.RLock()

    async def initialize(self) -> None:
        """创建表和索引（如果不存在）"""
        await _run_in_sqlite_pool(self._init_sync)
        logger.info(f"[HistoryStore] initialized: {self._db_path}")

    def _init_sync(self) -> None:
        """同步初始化：创建目录、连接数据库、建表建索引"""
        with self._conn_lock:
            # 确保目录存在
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)

            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            # 开启 WAL 模式，提高并发读写性能
            self._conn.execute("PRAGMA journal_mode=WAL;")

            self._conn.execute(_CREATE_TABLE_SQL)
            for idx_sql in _CREATE_INDEXES_SQL:
                self._conn.execute(idx_sql)
            self._conn.commit()

    # ================================================================
    # 记录操作
    # ================================================================

    async def record_add(
        self,
        memory_id: str,
        content: str,
        layer: str,
        isolation_key: str,
        actor_id: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> int:
        """记录 ADD 事件"""
        return await self._insert(
            memory_id=memory_id,
            isolation_key=isolation_key,
            event="ADD",
            new_memory=content,
            layer=layer,
            actor_id=actor_id,
            extra=extra,
        )

    async def record_update(
        self,
        memory_id: str,
        old_content: str,
        new_content: str,
        old_status: str,
        new_status: str,
        change_reason: str,
        isolation_key: str = "",
        actor_id: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> int:
        """记录 UPDATE 事件"""
        return await self._insert(
            memory_id=memory_id,
            isolation_key=isolation_key,
            event="UPDATE",
            old_memory=old_content,
            new_memory=new_content,
            old_status=old_status,
            new_status=new_status,
            change_reason=change_reason,
            actor_id=actor_id,
            extra=extra,
        )

    async def record_delete(
        self,
        memory_id: str,
        content: str,
        isolation_key: str,
        actor_id: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> int:
        """记录 DELETE 事件"""
        return await self._insert(
            memory_id=memory_id,
            isolation_key=isolation_key,
            event="DELETE",
            old_memory=content,
            actor_id=actor_id,
            extra=extra,
        )

    async def record_search(
        self,
        query: str,
        isolation_key: str,
        results_count: int,
        actor_id: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> int:
        """记录 SEARCH 事件"""
        search_extra = {"query": query, "results_count": results_count}
        if extra:
            search_extra.update(extra)
        return await self._insert(
            memory_id="",
            isolation_key=isolation_key,
            event="SEARCH",
            new_memory=query,
            actor_id=actor_id,
            extra=search_extra,
        )

    # ================================================================
    # 查询
    # ================================================================

    async def get_history(self, memory_id: str) -> List[Dict[str, Any]]:
        """
        获取某条记忆的完整变更历史。

        Args:
            memory_id: 记忆 ID

        Returns:
            按时间排序的历史记录列表
        """
        return await _run_in_sqlite_pool(self._get_history_sync, memory_id)

    def _get_history_sync(self, memory_id: str) -> List[Dict[str, Any]]:
        with self._conn_lock:
            if self._conn is None:
                return []
            cursor = self._conn.execute(
                "SELECT * FROM memory_history WHERE memory_id = ? ORDER BY created_at ASC",
                (memory_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    async def get_recent(
        self,
        isolation_key: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        获取某用户/隔离键的最近操作。

        Args:
            isolation_key: 调用方定义的隔离键
            limit: 返回数量上限

        Returns:
            按时间倒序的最近操作列表
        """
        return await _run_in_sqlite_pool(self._get_recent_sync, isolation_key, limit)

    def _get_recent_sync(self, isolation_key: str, limit: int) -> List[Dict[str, Any]]:
        with self._conn_lock:
            if self._conn is None:
                return []
            cursor = self._conn.execute(
                "SELECT * FROM memory_history WHERE isolation_key = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (isolation_key, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    async def get_recent_for_user(
        self,
        user_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """获取某 user 下所有 agent 的最近操作。

        isolation_key 形如 ``"{user_id}:{agent_id}"``。user_id 里的 LIKE
        通配符会被转义，保证只匹配这个 user，不会漏到 ``u1`` 之外。
        """
        prefix = _escape_like(f"{user_id}:") + "%"
        return await _run_in_sqlite_pool(
            self._get_recent_prefix_sync, prefix, limit,
        )

    def _get_recent_prefix_sync(
        self, pattern: str, limit: int,
    ) -> List[Dict[str, Any]]:
        with self._conn_lock:
            if self._conn is None:
                return []
            cursor = self._conn.execute(
                "SELECT * FROM memory_history WHERE isolation_key LIKE ? ESCAPE '\\' "
                "ORDER BY created_at DESC LIMIT ?",
                (pattern, limit),
            )
            return [dict(row) for row in cursor.fetchall()]

    async def count(self, isolation_key: str = "") -> int:
        """
        统计历史记录数量。

        Args:
            isolation_key: 可选，按隔离键过滤。为空则统计全部。

        Returns:
            记录数量
        """
        return await _run_in_sqlite_pool(self._count_sync, isolation_key)

    def _count_sync(self, isolation_key: str) -> int:
        with self._conn_lock:
            if self._conn is None:
                return 0
            if isolation_key:
                cursor = self._conn.execute(
                    "SELECT COUNT(*) FROM memory_history WHERE isolation_key = ?",
                    (isolation_key,),
                )
            else:
                cursor = self._conn.execute("SELECT COUNT(*) FROM memory_history")
            return cursor.fetchone()[0]

    async def close(self) -> None:
        """关闭数据库连接"""
        if self._conn is not None:
            await _run_in_sqlite_pool(self._close_sync)
            logger.info("[HistoryStore] closed")

    def _close_sync(self) -> None:
        with self._conn_lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    # ================================================================
    # 内部
    # ================================================================

    async def _insert(
        self,
        memory_id: str,
        isolation_key: str,
        event: str,
        old_memory: str = None,
        new_memory: str = None,
        old_status: str = None,
        new_status: str = None,
        change_reason: str = None,
        layer: str = None,
        actor_id: str = "",
        role: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> int:
        """插入一条历史记录，返回记录 ID"""
        return await _run_in_sqlite_pool(
            self._insert_sync,
            memory_id=memory_id,
            isolation_key=isolation_key,
            event=event,
            old_memory=old_memory,
            new_memory=new_memory,
            old_status=old_status,
            new_status=new_status,
            change_reason=change_reason,
            layer=layer,
            actor_id=actor_id,
            role=role,
            extra=extra,
        )

    def _insert_sync(
        self,
        memory_id: str,
        isolation_key: str,
        event: str,
        old_memory: str = None,
        new_memory: str = None,
        old_status: str = None,
        new_status: str = None,
        change_reason: str = None,
        layer: str = None,
        actor_id: str = "",
        role: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> int:
        with self._conn_lock:
            if self._conn is None:
                return -1
            now = _now_iso()
            extra_json = json.dumps(extra or {}, ensure_ascii=False)
            cursor = self._conn.execute(
                """INSERT INTO memory_history
                   (memory_id, isolation_key, event,
                    old_memory, new_memory, old_status, new_status,
                    change_reason, layer, actor_id, role, extra,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    memory_id,
                    isolation_key,
                    event,
                    old_memory,
                    new_memory,
                    old_status,
                    new_status,
                    change_reason,
                    layer,
                    actor_id,
                    role,
                    extra_json,
                    now,
                    now,
                ),
            )
            self._conn.commit()
            return cursor.lastrowid
