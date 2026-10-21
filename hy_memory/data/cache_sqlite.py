"""
Agent Memory V2 - SqliteCache

基于 Python 标准库 sqlite3 的零依赖审计/观测落库后端。
单机开箱即用。

设计模式跟随 history_store.py:
- 标准库 sqlite3 + _run_in_sqlite_pool()
- WAL 模式 + check_same_thread=False
- 每实例 threading.RLock 保护共享连接的全部操作与关闭

三张表:
- memory_operations / pipeline_logs / system_metrics: 审计与观测
"""

import concurrent.futures
import functools
import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any

from .cache_base import CacheBase
from ..config import MemoryConfig
from ..utils.async_executor import run_in_executor_guarded

logger = logging.getLogger(__name__)

# SQLite 独立线程池（不与 VDB/Graph 竞争）
_SQLITE_POOL_SIZE = 32
_sqlite_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_SQLITE_POOL_SIZE, thread_name_prefix="sqlite"
)


async def _run_in_sqlite_pool(func, *args, **kwargs):
    """在 SQLite 独立线程池中执行同步函数"""
    call = functools.partial(func, *args, **kwargs) if args or kwargs else func
    return await run_in_executor_guarded(_sqlite_executor, call)


# DDL
# 表名 / 列与 MySQL 分区版对齐（含 created_date），SQLite 不支持分区，仅普通建表。
_CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS memory_operations_v2 (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id   TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    agent_id     TEXT NOT NULL DEFAULT '',
    op           TEXT NOT NULL,       -- ADD / UPDATE
    memory_id    TEXT NOT NULL,       -- 操作后的节点 ID（ADD: 新节点 ID；UPDATE: 新节点 ID）
    old_memory_id TEXT,               -- UPDATE 时的旧节点 ID
    content      TEXT NOT NULL,       -- 最终写入的内容
    layer        TEXT NOT NULL DEFAULT '',
    reason       TEXT NOT NULL DEFAULT '',
    supersedes   TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL,
    created_date TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_memop_request ON memory_operations_v2(request_id);
CREATE INDEX IF NOT EXISTS idx_memop_memory  ON memory_operations_v2(memory_id);
CREATE INDEX IF NOT EXISTS idx_memop_user    ON memory_operations_v2(user_id);

CREATE TABLE IF NOT EXISTS pipeline_logs_v2 (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id   TEXT NOT NULL,
    namespace    TEXT NOT NULL DEFAULT '',
    user_id      TEXT NOT NULL,
    agent_id     TEXT NOT NULL DEFAULT '',
    session_id   TEXT NOT NULL DEFAULT '',
    step         TEXT NOT NULL,       -- EXTRACT / SEARCH_QUERY / RECONCILE / SUMMARY
    prompt       TEXT NOT NULL,       -- LLM prompt 原文
    response     TEXT NOT NULL,       -- LLM response 原文
    parsed       TEXT NOT NULL DEFAULT '',  -- 解析后的结构化结果 (JSON)
    memory_ids   TEXT NOT NULL DEFAULT '',  -- 关联的 memory_id 列表 (JSON array)，reconcile 阶段有值
    elapsed_ms   REAL NOT NULL DEFAULT 0,
    prompt_tokens    INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens     INTEGER NOT NULL DEFAULT 0,
    event_at_ms  INTEGER NOT NULL DEFAULT 0,  -- step 发生时刻（epoch 毫秒，与 JSONL log 同源，消费方自行转可读）
    created_at   TEXT NOT NULL,
    created_date TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_plog_request ON pipeline_logs_v2(request_id);
CREATE INDEX IF NOT EXISTS idx_plog_user    ON pipeline_logs_v2(user_id);

CREATE TABLE IF NOT EXISTS write_outcomes (
    request_id   TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL,
    agent_id     TEXT NOT NULL DEFAULT '',
    session_id   TEXT NOT NULL DEFAULT '',
    operation    TEXT NOT NULL,
    status       TEXT NOT NULL,
    result_json  TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_write_outcomes_owner
ON write_outcomes(user_id, agent_id, session_id);

CREATE TABLE IF NOT EXISTS system_metrics (
    minute_ts  TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_metrics_ts ON system_metrics(minute_ts);


-- Episode（内部会话）状态：跨 write 批次把"一件事"归到同一个 episode_id。
-- 只存三个值；划分规则（时间间隔）在 writer 侧，此处不含业务逻辑。
CREATE TABLE IF NOT EXISTS episode_state (
    episode_key    TEXT PRIMARY KEY,   -- user::agent::session
    user_id        TEXT NOT NULL DEFAULT '',
    agent_id       TEXT NOT NULL DEFAULT '',
    session_id     TEXT NOT NULL DEFAULT '',
    episode_id     TEXT NOT NULL DEFAULT '',
    last_write_ts  REAL NOT NULL DEFAULT 0,
    memory_count   INTEGER NOT NULL DEFAULT 0,
    updated_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_episode_user ON episode_state(user_id);
"""


class SqliteCache(CacheBase):
    """
    SQLite 审计/观测落库后端（memory_operations / pipeline_logs / system_metrics）。

    零依赖本地后端，适用于单机 / 开发环境。
    使用标准库 sqlite3，通过 _run_in_sqlite_pool() 包装为异步。
    """

    def __init__(self, config: MemoryConfig):
        self.config = config
        self._conn: Optional[sqlite3.Connection] = None
        # One connection is shared by the executor workers. Use the same lock
        # for reads, writes, initialization and close so close() cannot free
        # the connection while another worker is inside sqlite3.
        self._lock = threading.RLock()

        # 读取 db_path
        cache_cfg = getattr(config, "cache", None)
        if cache_cfg and getattr(cache_cfg, "db_path", None):
            self._db_path = cache_cfg.db_path
        else:
            from ..config import _default_data_dir
            self._db_path = os.getenv(
                "MEMORY_CACHE_DB_PATH",
                os.path.join(_default_data_dir(), "data", "cache.db"),
            )

    # ================================================================
    # 生命周期
    # ================================================================

    async def initialize(self) -> None:
        await _run_in_sqlite_pool(self._init_sync)
        logger.debug(f"SqliteCache initialized: {self._db_path}")

    def _init_sync(self) -> None:
        with self._lock:
            db_dir = os.path.dirname(self._db_path)
            if db_dir:
                os.makedirs(db_dir, exist_ok=True)
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.executescript(_CREATE_TABLES_SQL)
            self._migrate_pipeline_logs_columns()
            self._conn.commit()

    def _migrate_pipeline_logs_columns(self) -> None:
        """给旧库的 pipeline_logs_v2 补 namespace/session/event time 列。"""
        with self._lock:
            try:
                cols = {
                    row["name"]
                    for row in self._conn.execute("PRAGMA table_info(pipeline_logs_v2)")
                }
                _text_cols = ("namespace", "session_id")
                for col in _text_cols:
                    if col not in cols:
                        self._conn.execute(
                            f"ALTER TABLE pipeline_logs_v2 ADD COLUMN {col} TEXT NOT NULL DEFAULT ''"
                        )
                if "event_at_ms" not in cols:
                    self._conn.execute(
                        "ALTER TABLE pipeline_logs_v2 ADD COLUMN event_at_ms INTEGER NOT NULL DEFAULT 0"
                    )
            except Exception as e:
                logger.debug(f"[sqlite] pipeline_logs_v2 column migration skipped: {e}")

    async def close(self) -> None:
        if self._conn is not None:
            await _run_in_sqlite_pool(self._close_sync)
        logger.info("SqliteCache closed")

    def _close_sync(self) -> None:
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    # ================================================================
    # 统计
    # ================================================================

    async def get_stats(self) -> Dict[str, Any]:
        return {
            "backend": "sqlite",
            "db_path": self._db_path,
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
        """记录一条知识库变动操作（ADD / EVOLVE）"""
        from ..utils.pipeline_observability import is_memory_operations_enabled
        if not is_memory_operations_enabled():
            return True
        try:
            import json as _json
            from datetime import datetime as dt
            now = dt.now()
            created_at = now.isoformat()
            created_date = now.strftime("%Y-%m-%d")
            supersedes_str = _json.dumps(supersedes or [], ensure_ascii=False)

            def _insert():
                with self._lock:
                    self._conn.execute(
                        """INSERT INTO memory_operations_v2
                           (request_id, user_id, agent_id, op, memory_id, old_memory_id,
                            content, layer, reason, supersedes, created_at, created_date)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (request_id, user_id, agent_id, op, memory_id,
                         old_memory_id, content, layer, reason, supersedes_str,
                         created_at, created_date),
                    )
                    self._conn.commit()

            await _run_in_sqlite_pool(_insert)
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
        """
        查询知识库变动记录，支持按 request_id / memory_id / user_id 过滤。
        至少指定一个过滤条件。
        """
        try:
            def _query():
                with self._lock:
                    conditions = []
                    params = []
                    if request_id:
                        conditions.append("request_id = ?")
                        params.append(request_id)
                    if memory_id:
                        conditions.append("memory_id = ?")
                        params.append(memory_id)
                    if user_id:
                        conditions.append("user_id = ?")
                        params.append(user_id)

                    if not conditions:
                        return []

                    where = " AND ".join(conditions)
                    rows = self._conn.execute(
                        f"SELECT * FROM memory_operations_v2 WHERE {where} "
                        f"ORDER BY id DESC LIMIT ?",
                        params + [limit],
                    ).fetchall()
                    result = []
                    for r in rows:
                        row = dict(r)
                        # 反序列化 supersedes JSON 字符串 → list
                        import json as _json
                        raw_sup = row.get("supersedes", "")
                        try:
                            row["supersedes"] = _json.loads(raw_sup) if raw_sup else []
                        except Exception:
                            row["supersedes"] = []
                        result.append(row)
                    return result

            return await _run_in_sqlite_pool(_query)
        except Exception as e:
            logger.warning(f"get_memory_operations failed: {e}")
            return []

    # ================================================================
    # Pipeline Logs (LLM 调用链中间结果)
    # ================================================================

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
        """
        记录一条 pipeline 中间结果（EXTRACT / SEARCH_QUERY / RECONCILE / SUMMARY）。

        user_id 按 "__" 拆出 namespace 单独落库；session_id 与 user/agent 同为隔离维度。
        event_at_ms: step 发生时刻（epoch 毫秒，与 JSONL log timestamp 同源）；缺省回落入库时刻。
        """
        try:
            from datetime import datetime as dt
            from ..utils.pipeline_observability import split_namespace_user
            now = dt.now()
            created_at = now.isoformat()
            created_date = now.strftime("%Y-%m-%d")
            event_at_ms = int(event_at_ms) if event_at_ms else int(now.timestamp() * 1000)
            mem_ids_json = json.dumps(memory_ids or [])
            namespace, uid = split_namespace_user(user_id)

            def _insert():
                with self._lock:
                    self._conn.execute(
                        """INSERT INTO pipeline_logs_v2
                           (request_id, namespace, user_id, agent_id, session_id,
                            step, prompt, response,
                            parsed, memory_ids, elapsed_ms,
                            prompt_tokens, completion_tokens, total_tokens,
                            event_at_ms, created_at, created_date)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (request_id, namespace, uid, agent_id, session_id or "",
                         step, prompt, response,
                         parsed, mem_ids_json, elapsed_ms,
                         prompt_tokens, completion_tokens, total_tokens,
                         event_at_ms, created_at, created_date),
                    )
                    self._conn.commit()

            await _run_in_sqlite_pool(_insert)
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
        """
        查询 pipeline 中间结果日志。

        支持按 request_id / user_id / step 过滤，至少指定一个。
        """
        try:
            def _query():
                with self._lock:
                    conditions = []
                    params = []
                    if request_id:
                        conditions.append("request_id = ?")
                        params.append(request_id)
                    if user_id:
                        conditions.append("user_id = ?")
                        params.append(user_id)
                    if step:
                        conditions.append("step = ?")
                        params.append(step)

                    if not conditions:
                        return []

                    where = " AND ".join(conditions)
                    rows = self._conn.execute(
                        f"SELECT * FROM pipeline_logs_v2 WHERE {where} "
                        f"ORDER BY id ASC LIMIT ?",
                        params + [limit],
                    ).fetchall()
                    out = [dict(r) for r in rows]
                    # 派生可读的 event_at（ISO ms）供查询接口直接用；event_at_ms 为 0 时留空
                    from datetime import datetime as _dt
                    for _r in out:
                        _ms = _r.get("event_at_ms") or 0
                        _r["event_at"] = (
                            _dt.fromtimestamp(_ms / 1000.0).isoformat(timespec="milliseconds")
                            if _ms else ""
                        )
                    return out

            return await _run_in_sqlite_pool(_query)
        except Exception as e:
            logger.warning(f"get_pipeline_logs failed: {e}")
            return []

    # ================================================================
    # Durable write outcomes
    # ================================================================

    async def claim_write_outcome(
        self,
        request_id: str,
        user_id: str,
        agent_id: str,
        session_id: str,
        operation: str,
    ) -> Optional[Dict[str, Any]]:
        def _claim():
            with self._lock:
                now = datetime.now().isoformat()
                cursor = self._conn.execute(
                    """
                    INSERT OR IGNORE INTO write_outcomes (
                        request_id, user_id, agent_id, session_id, operation,
                        status, result_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, 'running', '{}', ?, ?)
                    """,
                    (request_id, user_id, agent_id, session_id, operation, now, now),
                )
                self._conn.commit()
                if cursor.rowcount == 1:
                    return None
                row = self._conn.execute(
                    "SELECT * FROM write_outcomes WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                return dict(row) if row else None

        existing = await _run_in_sqlite_pool(_claim)
        if existing is None:
            return None
        return await self.get_write_outcome(request_id)

    async def finish_write_outcome(
        self,
        request_id: str,
        status: str,
        result: Dict[str, Any],
    ) -> bool:
        def _finish():
            with self._lock:
                cursor = self._conn.execute(
                    """
                    UPDATE write_outcomes
                    SET status = ?, result_json = ?, updated_at = ?
                    WHERE request_id = ?
                    """,
                    (
                        status,
                        json.dumps(result, ensure_ascii=False, default=str),
                        datetime.now().isoformat(),
                        request_id,
                    ),
                )
                self._conn.commit()
                return cursor.rowcount == 1

        return await _run_in_sqlite_pool(_finish)

    async def get_write_outcome(self, request_id: str) -> Optional[Dict[str, Any]]:
        def _get():
            with self._lock:
                row = self._conn.execute(
                    "SELECT * FROM write_outcomes WHERE request_id = ?",
                    (request_id,),
                ).fetchone()
                return dict(row) if row else None

        row = await _run_in_sqlite_pool(_get)
        if row is None:
            return None
        try:
            row["result"] = json.loads(row.pop("result_json") or "{}")
        except (TypeError, json.JSONDecodeError):
            row["result"] = {}
        return row

    # ================================================================
    # System Metrics（分钟粒度落盘）
    # ================================================================

    async def store_metrics_minute(self, minute_ts: str, data: dict) -> None:
        """存储一个分钟桶的增量指标数据（UPSERT: 同一分钟多次写入会合并）"""
        try:
            def _store():
                with self._lock:
                    now = datetime.now().isoformat()
                    # UPSERT: 如果同一分钟已存在，合并数据
                    existing = self._conn.execute(
                        "SELECT data FROM system_metrics WHERE minute_ts = ?",
                        (minute_ts,),
                    ).fetchone()
                    if existing:
                        import json as _json
                        old_data = _json.loads(existing["data"])
                        merged = self._merge_metric_buckets(old_data, data)
                        self._conn.execute(
                            "UPDATE system_metrics SET data = ?, created_at = ? WHERE minute_ts = ?",
                            (_json.dumps(merged, default=str), now, minute_ts),
                        )
                    else:
                        self._conn.execute(
                            "INSERT INTO system_metrics (minute_ts, data, created_at) VALUES (?, ?, ?)",
                            (minute_ts, json.dumps(data, default=str), now),
                        )
                    self._conn.commit()
            await _run_in_sqlite_pool(_store)
        except Exception as e:
            logger.warning(f"store_metrics_minute failed: {e}")

    async def load_metrics_range(self, start_ts: str, end_ts: str) -> list:
        """读取指定时间范围内的分钟指标数据"""
        try:
            def _load():
                with self._lock:
                    rows = self._conn.execute(
                        "SELECT data FROM system_metrics WHERE minute_ts >= ? AND minute_ts <= ? ORDER BY minute_ts",
                        (start_ts, end_ts),
                    ).fetchall()
                    results = []
                    for row in rows:
                        try:
                            results.append(json.loads(row["data"]))
                        except (json.JSONDecodeError, TypeError):
                            pass
                    return results
            return await _run_in_sqlite_pool(_load)
        except Exception as e:
            logger.warning(f"load_metrics_range failed: {e}")
            return []

    async def cleanup_old_metrics(self, before_ts: str) -> None:
        """删除指定时间之前的 metrics 数据"""
        try:
            def _cleanup():
                with self._lock:
                    self._conn.execute(
                        "DELETE FROM system_metrics WHERE minute_ts < ?",
                        (before_ts,),
                    )
                    self._conn.commit()
            await _run_in_sqlite_pool(_cleanup)
        except Exception as e:
            logger.warning(f"cleanup_old_metrics failed: {e}")

    # ================================================================
    # Summary Buffer
    # ================================================================




    async def get_episode_state(self, episode_key: str) -> Optional[Dict[str, Any]]:
        try:
            def _query():
                with self._lock:
                    row = self._conn.execute(
                        "SELECT episode_id, last_write_ts, memory_count "
                        "FROM episode_state WHERE episode_key = ?",
                        (episode_key,),
                    ).fetchone()
                return dict(row) if row else None

            row = await _run_in_sqlite_pool(_query)
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

            def _write():
                with self._lock:
                    self._conn.execute(
                        """
                        INSERT INTO episode_state (
                            episode_key, user_id, agent_id, session_id,
                            episode_id, last_write_ts, memory_count, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(episode_key) DO UPDATE SET
                            episode_id = excluded.episode_id,
                            last_write_ts = excluded.last_write_ts,
                            memory_count = excluded.memory_count,
                            updated_at = excluded.updated_at
                        """,
                        (episode_key, user_id, agent_id, session_id, episode_id,
                         float(last_write_ts), int(memory_count),
                         _dt.now().isoformat()),
                    )
                    self._conn.commit()
                return True

            return await _run_in_sqlite_pool(_write)
        except Exception as e:
            logger.warning(f"upsert_episode_state failed: {e}")
            return False

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
