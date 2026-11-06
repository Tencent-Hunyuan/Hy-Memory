"""Default, dependency-free SQLite business repository."""

import logging
import os
import sqlite3
from contextlib import contextmanager

from .relational_repository import RelationalMemoryRepository
from .repository_schema import (
    _CREATE_TABLE_SQL, _CREATE_FACT_TABLE_SQL, _CREATE_TRACE_TABLE_SQL,
    _CREATE_INDEX_SQL, _INDEX_NAMES, _MIGRATE_COPY_SQL, _ADDED_COLUMNS,
)

logger = logging.getLogger(__name__)


class SqliteMemoryRepository(RelationalMemoryRepository):
    def __init__(self, db_path: str, trace_db_path: str) -> None:
        super().__init__()
        self._db_path = db_path
        self._trace_db_path = trace_db_path

    def initialize(self) -> None:
        try:
            self._init_sqlite_sync()
            self._init_trace_sqlite_sync()
        except BaseException:
            self.close()
            raise

    def _init_sqlite_sync(self) -> None:
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(
            self._db_path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._migrate_legacy_schema_sync()
        self._conn.executescript(_CREATE_TABLE_SQL)
        self._conn.executescript(_CREATE_FACT_TABLE_SQL)
        self._add_missing_columns_sync()
        for sql in _CREATE_INDEX_SQL:
            self._conn.execute(sql)

    def _init_trace_sqlite_sync(self) -> None:
        os.makedirs(os.path.dirname(self._trace_db_path) or ".", exist_ok=True)
        self._trace_conn = sqlite3.connect(
            self._trace_db_path, check_same_thread=False, isolation_level=None
        )
        self._trace_conn.row_factory = sqlite3.Row
        self._trace_conn.execute("PRAGMA journal_mode=WAL")
        self._trace_conn.execute("PRAGMA synchronous=NORMAL")
        self._trace_conn.executescript(_CREATE_TRACE_TABLE_SQL)

    def _add_missing_columns_sync(self) -> None:
        """给已存在的表补后加的列。ADD COLUMN 各版本 SQLite 都支持，无需重建。"""
        assert self._conn is not None
        for table, columns in _ADDED_COLUMNS.items():
            cur = self._conn.execute(f"PRAGMA table_info({table})")
            existing = {r["name"] for r in cur.fetchall()}
            for name, ddl in columns.items():
                if name not in existing:
                    logger.info(f"[coding-store] adding column {table}.{name}")
                    self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    def _migrate_legacy_schema_sync(self) -> None:
        """把旧 schema（memory_id 主键 + boundary_scope 列）就地重建为新 schema。

        走"建新表 + 拷数据 + 删旧表"而不是 ALTER：RENAME COLUMN 要 SQLite 3.25+、
        DROP COLUMN 要 3.35+，重建则不挑版本。experience_id 直接沿用旧 memory_id 的
        值，因此向量库那边 deterministic 的 node_id 不受影响。
        """
        assert self._conn is not None
        cur = self._conn.execute("PRAGMA table_info(coding_memory_meta)")
        cols = [r["name"] for r in cur.fetchall()]
        if not cols or "memory_id" not in cols:
            return

        logger.info("[coding-store] migrating legacy schema (memory_id → experience_id)")
        self._conn.execute("BEGIN")
        try:
            self._conn.execute(
                "ALTER TABLE coding_memory_meta RENAME TO coding_memory_meta_legacy"
            )
            # 索引跟着旧表走且重名，先清掉再让新表重建
            for name in _INDEX_NAMES:
                self._conn.execute(f"DROP INDEX IF EXISTS {name}")
            # 用 execute 而非 executescript：后者遇到未决事务会先隐式 COMMIT，
            # 会把这次迁移的原子性打断
            self._conn.execute(_CREATE_TABLE_SQL)
            self._conn.execute(_MIGRATE_COPY_SQL)
            self._conn.execute("DROP TABLE coding_memory_meta_legacy")
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise


    @contextmanager
    def _trace_transaction(self, user_id: str, agent_id: str):
        # Both variants share this file; serialize deduplication and retention.
        self._trace_conn.execute("BEGIN IMMEDIATE")
        try:
            yield
            self._trace_conn.execute("COMMIT")
        except BaseException:
            self._trace_conn.execute("ROLLBACK")
            raise
