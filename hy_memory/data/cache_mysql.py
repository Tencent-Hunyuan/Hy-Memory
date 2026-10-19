"""
Agent Memory V2 - MysqlCache

基于 aiomysql 的 MySQL 审计/观测落库后端。
适用于标准 MySQL 实例，支持多实例共享状态。

设计模式：
- aiomysql 原生 async（无需线程池包装）
- 连接池 aiomysql.create_pool
- autocommit=True

三张表（与 SQLite 版本一一对应）：
- memory_operations: 知识库变动日志
- pipeline_logs:     LLM 调用链中间结果
- system_metrics:    分钟粒度系统指标
"""

import json
import logging
import os
from datetime import datetime
from typing import Optional, List, Dict, Any

from .cache_base import CacheBase
from ..config import MemoryConfig

logger = logging.getLogger(__name__)

# DDL (MySQL syntax)
_CREATE_TABLES_SQL = [
    """
    CREATE TABLE IF NOT EXISTS memory_operations_v2 (
        id            BIGINT AUTO_INCREMENT,
        request_id    VARCHAR(128) NOT NULL,
        user_id       VARCHAR(128) NOT NULL,
        agent_id      VARCHAR(128) NOT NULL DEFAULT '',
        op            VARCHAR(32) NOT NULL,
        memory_id     VARCHAR(128) NOT NULL,
        old_memory_id VARCHAR(128) DEFAULT NULL,
        content       LONGTEXT NOT NULL,
        layer         VARCHAR(32) NOT NULL DEFAULT '',
        reason        TEXT NOT NULL,
        supersedes    TEXT NOT NULL,
        created_at    VARCHAR(64) NOT NULL,
        created_date  DATE NOT NULL,
        PRIMARY KEY (id, created_date),
        INDEX idx_memop_request (request_id),
        INDEX idx_memop_memory (memory_id),
        INDEX idx_memop_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    PARTITION BY RANGE (TO_DAYS(created_date)) (
        PARTITION pmax VALUES LESS THAN MAXVALUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pipeline_logs_v2 (
        id                BIGINT AUTO_INCREMENT,
        request_id        VARCHAR(128) NOT NULL,
        namespace         VARCHAR(128) NOT NULL DEFAULT '',
        user_id           VARCHAR(128) NOT NULL,
        agent_id          VARCHAR(128) NOT NULL DEFAULT '',
        session_id        VARCHAR(128) NOT NULL DEFAULT '',
        step              VARCHAR(64) NOT NULL,
        prompt            LONGTEXT NOT NULL,
        response          LONGTEXT NOT NULL,
        parsed            LONGTEXT NOT NULL,
        memory_ids        TEXT NOT NULL,
        elapsed_ms        DOUBLE NOT NULL DEFAULT 0,
        prompt_tokens     INT NOT NULL DEFAULT 0,
        completion_tokens INT NOT NULL DEFAULT 0,
        total_tokens      INT NOT NULL DEFAULT 0,
        event_at_ms       BIGINT NOT NULL DEFAULT 0,
        created_at        VARCHAR(64) NOT NULL,
        created_date      DATE NOT NULL,
        PRIMARY KEY (id, created_date),
        INDEX idx_plog_request (request_id),
        INDEX idx_plog_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    PARTITION BY RANGE (TO_DAYS(created_date)) (
        PARTITION pmax VALUES LESS THAN MAXVALUE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS system_metrics (
        minute_ts   VARCHAR(32) NOT NULL,
        data        LONGTEXT NOT NULL,
        created_at  VARCHAR(64) NOT NULL,
        PRIMARY KEY (minute_ts)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    # Episode（内部会话）状态：跨 write 批次把"一件事"归到同一个 episode_id。
    # 缺了这张表，get_episode_state 永远返回 None → 每次写入都开新 episode，
    # 划分等于没生效（线上实测 8798 次决策全是 new）。
    """
    CREATE TABLE IF NOT EXISTS episode_state (
        episode_key    VARCHAR(255) NOT NULL,
        user_id        VARCHAR(255) NOT NULL DEFAULT '',
        agent_id       VARCHAR(255) NOT NULL DEFAULT '',
        session_id     VARCHAR(255) NOT NULL DEFAULT '',
        episode_id     VARCHAR(64)  NOT NULL DEFAULT '',
        last_write_ts  DOUBLE       NOT NULL DEFAULT 0,
        memory_count   INT          NOT NULL DEFAULT 0,
        updated_at     VARCHAR(64)  NOT NULL,
        PRIMARY KEY (episode_key),
        KEY idx_episode_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


class MysqlCache(CacheBase):
    """
    MySQL 审计/观测落库后端（memory_operations / pipeline_logs / system_metrics）。

    基于 aiomysql 的原生异步 MySQL 后端。
    """

    def __init__(self, config: MemoryConfig):
        self.config = config
        self._pool = None  # aiomysql.Pool

        # 读取 MySQL 连接参数
        cache_cfg = getattr(config, "cache", None)
        self._host = getattr(cache_cfg, "mysql_host", None) or os.getenv("MEMORY_MYSQL_HOST", "localhost")
        self._port = getattr(cache_cfg, "mysql_port", None) or int(os.getenv("MEMORY_MYSQL_PORT", "3306"))
        self._user = getattr(cache_cfg, "mysql_user", None) or os.getenv("MEMORY_MYSQL_USER", "root")
        self._password = getattr(cache_cfg, "mysql_password", None) or os.getenv("MEMORY_MYSQL_PASSWORD", "")
        self._database = getattr(cache_cfg, "mysql_database", None) or os.getenv("MEMORY_MYSQL_DATABASE", "hy_memory")
        self._pool_size = getattr(cache_cfg, "mysql_pool_size", None) or int(os.getenv("MEMORY_MYSQL_POOL_SIZE", "10"))
        self._pool_recycle = getattr(cache_cfg, "mysql_pool_recycle", None) or int(os.getenv("MEMORY_MYSQL_POOL_RECYCLE", "3600"))

    # ================================================================
    # 生命周期
    # ================================================================

    async def initialize(self) -> None:
        """立即创建 pool（必须在正确的 event loop 上调用，即 _LoopThread 的 loop）"""
        import aiomysql
        self._pool = await aiomysql.create_pool(
            host=self._host,
            port=self._port,
            user=self._user,
            password=self._password,
            db=self._database,
            minsize=2,
            maxsize=self._pool_size,
            pool_recycle=self._pool_recycle,
            autocommit=True,
            charset='utf8mb4',
        )
        await self._ensure_tables()
        logger.info(
            f"MysqlCache initialized: {self._user}@{self._host}:{self._port}/{self._database} "
            f"pool_size={self._pool_size}"
        )

    async def _ensure_tables(self) -> None:
        """确保所有表存在"""
        for ddl in _CREATE_TABLES_SQL:
            await self._execute(ddl)
        await self._migrate_pipeline_logs_columns()

    async def _migrate_pipeline_logs_columns(self) -> None:
        """给旧库的 pipeline_logs_v2 补 namespace/session/event time 列。

        用 AFTER 指定列位置，与 CREATE TABLE 一致：
        namespace 紧跟 request_id、session_id 紧跟 agent_id。
        """
        migrations = (
            ("namespace", "ALTER TABLE pipeline_logs_v2 ADD COLUMN namespace "
                       "VARCHAR(128) NOT NULL DEFAULT '' AFTER request_id"),
            ("session_id", "ALTER TABLE pipeline_logs_v2 ADD COLUMN session_id "
                           "VARCHAR(128) NOT NULL DEFAULT '' AFTER agent_id"),
            ("event_at_ms", "ALTER TABLE pipeline_logs_v2 ADD COLUMN event_at_ms "
                            "BIGINT NOT NULL DEFAULT 0 AFTER total_tokens"),
        )
        for col, ddl in migrations:
            try:
                await self._execute(ddl)
            except Exception as e:
                # 已存在 (Duplicate column) 等 → 忽略
                logger.debug(f"[mysql] add column {col} skipped: {e}")

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
        logger.info("MysqlCache closed")

    # ================================================================
    # 内部 DB helpers
    # ================================================================

    async def _execute(self, sql: str, args=None) -> int:
        """执行写操作，返回 affected rows"""
        async with self._pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                return cur.rowcount

    async def _fetchone(self, sql: str, args=None) -> Optional[Dict[str, Any]]:
        """查询单行，返回 dict 或 None"""
        import aiomysql
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, args)
                row = await cur.fetchone()
                # 防御：极端情况下 DictCursor 可能返回 tuple
                if row is not None and not isinstance(row, dict):
                    cols = [d[0] for d in cur.description] if cur.description else []
                    row = dict(zip(cols, row))
                return row

    async def _fetchall(self, sql: str, args=None) -> List[Dict[str, Any]]:
        """查询多行，返回 list of dict"""
        import aiomysql
        async with self._pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, args)
                rows = await cur.fetchall()
                # 防御：确保返回 list of dict
                if rows and not isinstance(rows[0], dict):
                    cols = [d[0] for d in cur.description] if cur.description else []
                    rows = [dict(zip(cols, r)) for r in rows]
                return rows

    # ================================================================
    # 统计
    # ================================================================

    async def get_stats(self) -> Dict[str, Any]:
        return {
            "backend": "mysql",
            "host": self._host,
            "database": self._database,
        }

    # ================================================================
    # Memory Operations Log
    # ================================================================

    async def store_memory_operation(
        self,
        request_id: str,
        user_id: str,
        agent_id: str,
        op: str,
        memory_id: str,
        content: str,
        layer: str = "",
        old_memory_id: Optional[str] = None,
        reason: str = "",
        supersedes: Optional[List[str]] = None,
    ) -> bool:
        from ..utils.pipeline_observability import is_memory_operations_enabled
        if not is_memory_operations_enabled():
            return True
        try:
            now = datetime.now()
            created_at = now.isoformat()
            created_date = now.strftime("%Y-%m-%d")
            supersedes_str = json.dumps(supersedes or [], ensure_ascii=False)
            await self._execute(
                """INSERT INTO memory_operations_v2
                   (request_id, user_id, agent_id, op, memory_id, old_memory_id,
                    content, layer, reason, supersedes, created_at, created_date)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (request_id, user_id, agent_id, op, memory_id,
                 old_memory_id, content, layer, reason, supersedes_str,
                 created_at, created_date),
            )
            return True
        except Exception as e:
            logger.warning(f"store_memory_operation failed: {e}")
            return False

    async def get_memory_operations(
        self,
        request_id: Optional[str] = None,
        memory_id: Optional[str] = None,
        user_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        try:
            conditions = []
            params = []
            if request_id:
                conditions.append("request_id = %s")
                params.append(request_id)
            if memory_id:
                conditions.append("memory_id = %s")
                params.append(memory_id)
            if user_id:
                conditions.append("user_id = %s")
                params.append(user_id)

            if not conditions:
                return []

            where = " AND ".join(conditions)
            rows = await self._fetchall(
                f"SELECT * FROM memory_operations_v2 WHERE {where} "
                f"ORDER BY id DESC LIMIT %s",
                params + [limit],
            )
            # 反序列化 supersedes
            for row in rows:
                raw_sup = row.get("supersedes", "")
                try:
                    row["supersedes"] = json.loads(raw_sup) if raw_sup else []
                except Exception:
                    row["supersedes"] = []
            return rows
        except Exception as e:
            logger.warning(f"get_memory_operations failed: {e}")
            return []

    # ================================================================
    # Pipeline Logs
    # ================================================================

    # ── Episode（内部会话）状态 ──

    async def get_episode_state(self, episode_key: str) -> Optional[Dict[str, Any]]:
        try:
            row = await self._fetchone(
                "SELECT episode_id, last_write_ts, memory_count "
                "FROM episode_state WHERE episode_key = %s",
                (episode_key,),
            )
            if not row:
                return None
            return {
                "episode_id": row.get("episode_id") or "",
                "last_write_ts": float(row.get("last_write_ts") or 0),
                "memory_count": int(row.get("memory_count") or 0),
            }
        except Exception as e:
            logger.warning(f"get_episode_state failed: {e}")
            return None

    async def upsert_episode_state(
        self,
        episode_key: str,
        *,
        user_id: str,
        agent_id: str,
        session_id: str,
        episode_id: str,
        last_write_ts: float,
        memory_count: int,
    ) -> bool:
        try:
            from datetime import datetime as _dt

            await self._execute(
                """
                INSERT INTO episode_state (
                    episode_key, user_id, agent_id, session_id,
                    episode_id, last_write_ts, memory_count, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    episode_id = VALUES(episode_id),
                    last_write_ts = VALUES(last_write_ts),
                    memory_count = VALUES(memory_count),
                    updated_at = VALUES(updated_at)
                """,
                (episode_key, user_id, agent_id, session_id, episode_id,
                 float(last_write_ts), int(memory_count), _dt.now().isoformat()),
            )
            return True
        except Exception as e:
            logger.warning(f"upsert_episode_state failed: {e}")
            return False

    async def store_pipeline_log(
        self,
        request_id: str,
        user_id: str,
        agent_id: str,
        step: str,
        prompt: str,
        response: str,
        parsed: str = "",
        memory_ids: Optional[List[str]] = None,
        elapsed_ms: float = 0,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        session_id: str = "",
        event_at_ms: int = 0,
    ) -> bool:
        try:
            from ..utils.pipeline_observability import split_namespace_user
            now = datetime.now()
            created_at = now.isoformat()
            created_date = now.strftime("%Y-%m-%d")
            event_at_ms = int(event_at_ms) if event_at_ms else int(now.timestamp() * 1000)
            mem_ids_json = json.dumps(memory_ids or [])
            namespace, uid = split_namespace_user(user_id)
            await self._execute(
                """INSERT INTO pipeline_logs_v2
                   (request_id, namespace, user_id, agent_id, session_id,
                    step, prompt, response,
                    parsed, memory_ids, elapsed_ms,
                    prompt_tokens, completion_tokens, total_tokens,
                    event_at_ms, created_at, created_date)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (request_id, namespace, uid, agent_id, session_id or "",
                 step, prompt, response,
                 parsed, mem_ids_json, elapsed_ms,
                 prompt_tokens, completion_tokens, total_tokens,
                 event_at_ms, created_at, created_date),
            )
            return True
        except Exception as e:
            logger.warning(f"store_pipeline_log failed: {e}")
            return False

    async def get_pipeline_logs(
        self,
        request_id: Optional[str] = None,
        user_id: Optional[str] = None,
        step: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        try:
            conditions = []
            params = []
            if request_id:
                conditions.append("request_id = %s")
                params.append(request_id)
            if user_id:
                conditions.append("user_id = %s")
                params.append(user_id)
            if step:
                conditions.append("step = %s")
                params.append(step)

            if not conditions:
                return []

            where = " AND ".join(conditions)
            rows = await self._fetchall(
                f"SELECT * FROM pipeline_logs_v2 WHERE {where} "
                f"ORDER BY id ASC LIMIT %s",
                params + [limit],
            )
            # 派生可读的 event_at（ISO ms）供查询接口直接用；event_at_ms 为 0 时留空
            for _r in rows:
                _ms = _r.get("event_at_ms") or 0
                _r["event_at"] = (
                    datetime.fromtimestamp(_ms / 1000.0).isoformat(timespec="milliseconds")
                    if _ms else ""
                )
            return rows
        except Exception as e:
            logger.warning(f"get_pipeline_logs failed: {e}")
            return []

    # ================================================================
    # System Metrics（分钟粒度落盘）
    # ================================================================

    async def store_metrics_minute(self, minute_ts: str, data: dict) -> None:
        import aiomysql

        try:
            now = datetime.now().isoformat()
            # Read-merge-write must be one transaction: two instances flushing
            # the same minute used to overwrite each other's counters, and a
            # concurrent insert could lose the whole batch to a duplicate key.
            for attempt in range(2):
                try:
                    async with self._pool.acquire() as conn:
                        await conn.begin()
                        try:
                            async with conn.cursor(aiomysql.DictCursor) as cur:
                                await cur.execute(
                                    "SELECT data FROM system_metrics "
                                    "WHERE minute_ts = %s FOR UPDATE",
                                    (minute_ts,),
                                )
                                row = await cur.fetchone()
                                if row is not None:
                                    old_raw = (
                                        row["data"]
                                        if isinstance(row, dict)
                                        else row[0]
                                    )
                                    merged = self._merge_metric_buckets(
                                        json.loads(old_raw), data,
                                    )
                                    await cur.execute(
                                        "UPDATE system_metrics SET data = %s, "
                                        "created_at = %s WHERE minute_ts = %s",
                                        (
                                            json.dumps(merged, default=str),
                                            now,
                                            minute_ts,
                                        ),
                                    )
                                else:
                                    await cur.execute(
                                        "INSERT INTO system_metrics "
                                        "(minute_ts, data, created_at) "
                                        "VALUES (%s, %s, %s)",
                                        (
                                            minute_ts,
                                            json.dumps(data, default=str),
                                            now,
                                        ),
                                    )
                            await conn.commit()
                            return
                        except Exception:
                            await conn.rollback()
                            raise
                except (aiomysql.IntegrityError, aiomysql.OperationalError) as error:
                    code = error.args[0] if error.args else None
                    deadlock = code in (1205, 1213)
                    if attempt or (isinstance(error, aiomysql.OperationalError) and not deadlock):
                        raise
                    # A competing writer inserted the row, or InnoDB chose us
                    # as the deadlock victim. Retry so this batch merges.
                    continue
        except Exception as e:
            logger.warning(f"store_metrics_minute failed: {e}")

    async def load_metrics_range(self, start_ts: str, end_ts: str) -> list:
        try:
            rows = await self._fetchall(
                "SELECT data FROM system_metrics WHERE minute_ts >= %s AND minute_ts <= %s ORDER BY minute_ts",
                (start_ts, end_ts),
            )
            results = []
            for row in rows:
                try:
                    results.append(json.loads(row["data"]))
                except (json.JSONDecodeError, TypeError):
                    pass
            return results
        except Exception as e:
            logger.warning(f"load_metrics_range failed: {e}")
            return []

    async def cleanup_old_metrics(self, before_ts: str) -> None:
        try:
            await self._execute(
                "DELETE FROM system_metrics WHERE minute_ts < %s", (before_ts,)
            )
        except Exception as e:
            logger.warning(f"cleanup_old_metrics failed: {e}")

    # ================================================================
    # Summary Buffer
    # ================================================================




    @staticmethod
    def _merge_metric_buckets(old: dict, new: dict) -> dict:
        """合并两个 metric bucket（同一分钟多次 flush）"""
        merged = dict(old)
        for key in ("sys1_started", "sys1_completed", "sys1_failed",
                    "sys2_started", "sys2_completed", "sys2_failed",
                    "vdb_ops", "graph_ops"):
            merged[key] = merged.get(key, 0) + new.get(key, 0)
        for key in ("vdb_ops_sum_ms", "graph_ops_sum_ms"):
            merged[key] = merged.get(key, 0) + new.get(key, 0)
        # timing sums
        for bucket_key in ("sys1_timing_sums", "sys2_timing_sums"):
            old_sums = merged.get(bucket_key, {})
            new_sums = new.get(bucket_key, {})
            for k, v in new_sums.items():
                old_sums[k] = old_sums.get(k, 0) + v
            merged[bucket_key] = old_sums
        return merged
