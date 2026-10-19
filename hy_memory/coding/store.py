# -*- coding: utf-8 -*-
"""
Coding Memory Store - 双层物理存储

- SQLite (coding_memory.db)        — coding_memory_meta 表，每条 memory 一行
- VDB (独立 collection)              — 每个 search key 一行向量，由 _vec_collection 名隔离
                                       从 chat collection 派生独立 collection 名

每条 CodingMemory 在 VDB 里有 1 + N 行（task + N 个 search_keys）。
SQLite 里有 1 行（含 search_keys JSON 字段，便于 update 时知道有几行 keys）。

写入流程:
  1. SQLite upsert
  2. embed_batch(task + search_keys)
  3. VDB upsert_batch（每行一个 MemoryNode，custom 字段携带 coding_experience_id 等）

更新流程（覆盖式）:
  1. 拿旧 search_keys 长度 → 删旧 keys 的 VDB node_id
  2. SQLite update
  3. 重新走写入步骤 2-3

删除流程:
  1. 拿 search_keys 长度 → 删全部 VDB node_id
  2. SQLite delete

对外使用方式见 docs/scene_coding_usage.md。
"""

import concurrent.futures
import functools
import json
import logging
import math
import os
import sqlite3
import threading
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from ..config import MemoryConfig
from ..core.embed_service import EmbedService
from ..data.vector_store import create_vector_store
from ..data.vector_store_base import VectorStoreBase
from ..models.memory import MemoryLayer, MemoryNode, MemoryStatus
from ..utils.async_executor import run_in_executor_guarded
from .types import CodingMemory, WorkingFact

logger = logging.getLogger(__name__)


# ================================================================
# SQLite 线程池
# ================================================================

_CODING_SQLITE_POOL_SIZE = 16
_coding_sqlite_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_CODING_SQLITE_POOL_SIZE,
    thread_name_prefix="coding-sqlite",
)


async def _run_in_pool(func, *args, **kwargs):
    call = functools.partial(func, *args, **kwargs) if args or kwargs else func
    return await run_in_executor_guarded(_coding_sqlite_executor, call)


# ================================================================
# DDL
# ================================================================

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS coding_memory_meta (
    experience_id    TEXT PRIMARY KEY,
    user_id          TEXT NOT NULL,
    agent_id         TEXT NOT NULL DEFAULT 'default_agent',
    task             TEXT NOT NULL,
    search_keys      TEXT NOT NULL DEFAULT '[]',
    solution         TEXT NOT NULL,
    boundary_envs    TEXT NOT NULL DEFAULT '',
    workspace_id     TEXT,
    branch           TEXT,
    session_id       TEXT,
    files            TEXT NOT NULL DEFAULT '[]',
    source_fact_ids  TEXT NOT NULL DEFAULT '[]',
    confidence       REAL NOT NULL DEFAULT 0.7,
    source           TEXT NOT NULL DEFAULT 'default',
    extraction_source TEXT NOT NULL DEFAULT 'auto_extract',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""

# 事实层（episodic）。与经验表同库不同表；无 TTL，靠 SUPERSEDE 软删保新鲜。
_CREATE_FACT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS working_fact (
    fact_id                TEXT PRIMARY KEY,
    user_id                TEXT NOT NULL,
    agent_id               TEXT NOT NULL DEFAULT 'default_agent',
    session_id             TEXT,
    workspace_id           TEXT,
    branch                 TEXT,
    source                 TEXT NOT NULL DEFAULT 'default',
    statement              TEXT NOT NULL,
    files                  TEXT NOT NULL DEFAULT '[]',
    subject                TEXT NOT NULL DEFAULT '',
    details                TEXT NOT NULL DEFAULT '',
    fact_link              TEXT,
    derived_experience_ids TEXT NOT NULL DEFAULT '[]',
    status                 TEXT NOT NULL DEFAULT 'active',
    fact_chain_summary     TEXT NOT NULL DEFAULT '',
    created_at             TEXT NOT NULL
);
-- 反查前驱（谁的 fact_link 指向我）走这个索引，写演变总结时要顺着链往回走
CREATE INDEX IF NOT EXISTS idx_working_fact_link ON working_fact(fact_link);
"""

_CREATE_TRACE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS coding_trace (
    request_id    TEXT PRIMARY KEY,
    user_id       TEXT NOT NULL,
    agent_id      TEXT NOT NULL DEFAULT 'default_agent',
    session_id    TEXT,
    workspace_id  TEXT,
    branch        TEXT,
    source        TEXT NOT NULL DEFAULT 'default',
    messages_json TEXT NOT NULL DEFAULT '[]',
    created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trace_user_created
    ON coding_trace(user_id, agent_id, source, created_at DESC);
"""

_INDEX_NAMES = ("idx_coding_user", "idx_coding_user_workspace", "idx_coding_updated")

_CREATE_INDEX_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_coding_user ON coding_memory_meta(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_coding_user_workspace ON coding_memory_meta(user_id, workspace_id);",
    "CREATE INDEX IF NOT EXISTS idx_coding_user_source ON coding_memory_meta(user_id, source);",
    "CREATE INDEX IF NOT EXISTS idx_coding_updated ON coding_memory_meta(updated_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_fact_user ON working_fact(user_id);",
    "CREATE INDEX IF NOT EXISTS idx_fact_user_status ON working_fact(user_id, status);",
    "CREATE INDEX IF NOT EXISTS idx_fact_user_workspace ON working_fact(user_id, workspace_id);",
    "CREATE INDEX IF NOT EXISTS idx_fact_user_source ON working_fact(user_id, source);",
    "CREATE INDEX IF NOT EXISTS idx_fact_created ON working_fact(created_at DESC);",
]

# 旧 schema：主键叫 memory_id，且带一个不再使用的 boundary_scope 列。
_MIGRATE_COPY_SQL = """
INSERT INTO coding_memory_meta (
    experience_id, user_id, agent_id, task, search_keys, solution,
    boundary_envs, workspace_id, branch, session_id,
    files, confidence, source, created_at, updated_at
)
SELECT
    memory_id, user_id, agent_id, task, search_keys, solution,
    boundary_envs, workspace_id, branch, session_id,
    files, confidence, source, created_at, updated_at
FROM coding_memory_meta_legacy;
"""

# 后加的列：老库用 ADD COLUMN 补，不必重建表
_ADDED_COLUMNS = {
    "coding_memory_meta": {
        "source_fact_ids": "TEXT NOT NULL DEFAULT '[]'",
        "extraction_source": "TEXT NOT NULL DEFAULT 'auto_extract'",
    },
    # 早期版本存过一列 fact_chain（机械累积的历代说法），已废弃：同样的信息顺着
    # fact_link 反查就有。老库里那一列留着不管，有默认值不影响插入。
    "working_fact": {
        "fact_chain_summary": "TEXT NOT NULL DEFAULT ''",
        # 事实层比经验层晚补这一列，老库里已有的行会是 NULL。作用域过滤按
        # "NULL = 没记录分支"处理，不当成"记录了别的分支"，见 _scope_where。
        "branch": "TEXT",
        # 覆盖闸门的硬锚点，和它配套的可寻址原文。老库里的行是空串：subject 空
        # 就退回纯语义 + LLM 判覆盖（改动前的行为），details 空就只有摘要可读。
        "subject": "TEXT NOT NULL DEFAULT ''",
        "details": "TEXT NOT NULL DEFAULT ''",
        "source": "TEXT NOT NULL DEFAULT 'default'",
    },
}


# ================================================================
# workspace / branch 作用域过滤
#
# 一套规则，经验层与事实层、召回（search）与枚举（list）共用：
#   - workspace_id 为空 → 不过滤，全量召回。调用方没传就是维持现状。
#   - workspace_id 非空 → 精确匹配。NULL 是"不属于任何 repo"，排除掉。
#   - branch 只在 workspace_id 非空时才叠加（与 list_memories 既有的组合语义
#     一致），单独传 branch 不生效。
#   - branch 容 NULL。branch IS NULL 的行是"没记录分支"，不是"记录了别的分支"，
#     筛掉它会在两处误杀：事实表这一列是后补的，老库里整表都是 NULL；写入方没
#     传 branch 的新行也是 NULL。branch 是 workspace 内部的细分，绝大多数经验
#     本来就与分支无关，宁可多给几条不确定的，也不能让它们变成召不回来。
# ================================================================

def _scope_where(
    workspace_id: Optional[str], branch: Optional[str]
) -> Tuple[str, List[Any]]:
    """把作用域条件翻译成 SQL 布尔表达式 + 绑定参数。不过滤时返回恒真的 "1"。"""
    if not workspace_id:
        return "1", []
    sql = "workspace_id = ?"
    params: List[Any] = [workspace_id]
    if branch:
        sql += " AND (branch = ? OR branch IS NULL)"
        params.append(branch)
    return sql, params


def _scope_matches(
    row_workspace_id: Optional[str],
    row_branch: Optional[str],
    workspace_id: Optional[str],
    branch: Optional[str],
) -> bool:
    """_scope_where 的 Python 等价，用于已经取到对象之后再过一道同样的规则。"""
    if not workspace_id:
        return True
    if row_workspace_id != workspace_id:
        return False
    if branch and row_branch is not None and row_branch != branch:
        return False
    return True


# 作用域过滤下推不到向量库：chroma（默认后端）把 custom 整体 JSON 序列化成一个
# metadata 字段，没法按 custom.coding_workspace_id 建条件；qdrant 能做到，但一个
# "只有部分后端才真生效"的硬过滤比没有更危险。所以过滤放在 SQLite —— 它才是这两
# 列的权威来源 —— 向量那边按过滤的选择率放大取数，让过滤不吃召回。
#
# 放大倍数不是拍的：total/scoped 就是从权威表数出来的选择率倒数，按它取数，ANN
# 返回的 top-N 里期望仍有 top 条落在作用域内。作用域覆盖了该 user 大半数据时倍数
# 接近 1；只占一小片时会一路放大到把这个 user 的行基本取全，此时过滤是精确的。
# 上限只为兜住"单 user 数据量病态大 + 作用域极窄"这一种组合。
_SCOPE_OVERFETCH_MAX_ROWS = 2000


def _scope_overfetch(top: int, scoped: int, total: int) -> int:
    """按作用域选择率放大向量取数条数。"""
    if scoped <= 0 or scoped >= total:
        return top
    scaled = math.ceil(top * total / scoped)
    return max(top, min(scaled, _SCOPE_OVERFETCH_MAX_ROWS))


# ================================================================
# Helpers
# ================================================================

def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _vec_node_id(experience_id: str, key_idx: int) -> str:
    return f"{experience_id}:{key_idx}"


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        # 去掉末尾 Z
        return datetime.fromisoformat(s.rstrip("Z"))
    except Exception:
        return None


def _row_to_memory(row: sqlite3.Row) -> CodingMemory:
    """SQLite row → CodingMemory"""
    return CodingMemory(
        experience_id=row["experience_id"],
        user_id=row["user_id"],
        agent_id=row["agent_id"],
        task=row["task"],
        search_keys=json.loads(row["search_keys"] or "[]"),
        solution=row["solution"],
        boundary_envs=row["boundary_envs"],
        workspace_id=row["workspace_id"],
        branch=row["branch"],
        session_id=row["session_id"],
        files=json.loads(row["files"] or "[]"),
        source_fact_ids=json.loads(row["source_fact_ids"] or "[]"),
        confidence=row["confidence"],
        source=row["source"],
        extraction_source=row["extraction_source"],
        created_at=_parse_dt(row["created_at"]),
        updated_at=_parse_dt(row["updated_at"]),
    )


def _row_to_fact(row: sqlite3.Row) -> WorkingFact:
    """SQLite row → WorkingFact"""
    return WorkingFact(
        fact_id=row["fact_id"],
        user_id=row["user_id"],
        agent_id=row["agent_id"],
        session_id=row["session_id"],
        workspace_id=row["workspace_id"],
        branch=(row["branch"] if "branch" in row.keys() else None),
        source=row["source"],
        statement=row["statement"],
        files=json.loads(row["files"] or "[]"),
        # 老库刚 ADD COLUMN 时可能取不到这两列，用 keys() 兜一下（同 fact_chain_summary）
        subject=(row["subject"] or "" if "subject" in row.keys() else ""),
        details=(row["details"] or "" if "details" in row.keys() else ""),
        fact_link=row["fact_link"],
        derived_experience_ids=json.loads(row["derived_experience_ids"] or "[]"),
        status=row["status"],
        # 老库刚 ADD COLUMN 时可能取不到这一列，用 keys() 兜一下
        fact_chain_summary=(row["fact_chain_summary"] or ""
                            if "fact_chain_summary" in row.keys() else ""),
        created_at=_parse_dt(row["created_at"]),
    )


def _derive_coding_collection_name(base: str, suffix: str = "") -> str:
    """从 chat collection 派生 coding_keys collection。

    suffix 是**变体后缀**（scene="coding" 传 "_cv"，working 传 ""）。为空时返回值
    与加这个参数之前逐字相同 —— working 的集合名不能变，否则已落盘的库读不到。
    """
    base = (base or "agent_memories").strip()
    # 维度后缀由 VectorStoreBase 自动拼接，这里只在 base_name 上加 _coding_keys
    return f"{base}_coding_keys{suffix}"


def _derive_fact_collection_name(base: str, suffix: str = "") -> str:
    """从 chat collection 派生事实层 collection。suffix 同上。"""
    base = (base or "agent_memories").strip()
    return f"{base}_working_facts{suffix}"


def _variant_db_path(db_path: str, suffix: str) -> str:
    """给 sqlite 路径加变体后缀。suffix 为空时原样返回。

    coding_memory.db → coding_memory_cv.db
    """
    if not suffix:
        return db_path
    root, ext = os.path.splitext(db_path)
    return f"{root}{suffix}{ext}"


def _clone_config_with_collection(config: MemoryConfig, derive, suffix: str = "") -> MemoryConfig:
    """深拷贝 config 并把 collection_name 换成 derive() 派生出的名字。"""
    cfg = deepcopy(config)
    base = cfg.vector_store.collection_name or "agent_memories"
    cfg.vector_store.collection_name = derive(base, suffix)
    return cfg


# ================================================================
# Store
# ================================================================

class CodingMemoryStore:
    """
    Coding Memory 双层存储。

    必须先 await initialize() 再用。close() 释放资源。
    """

    def __init__(
        self,
        config: MemoryConfig,
        embed_service: EmbedService,
        db_path: Optional[str] = None,
        variant: str = "working",
    ):
        self.config = config
        self.embed_service = embed_service
        # 变体后缀。working → ""（路径和集合名与改动前逐字相同，已落盘的库照旧能读）；
        # coding → "_cv"（独立 sqlite + 独立两个向量集合）。
        #
        # 为什么必须分库而不是加一个 variant 列做过滤：检索是**先**在向量集合里取
        # top-K、**再**用 sqlite 过滤。两个变体的 fact 混在同一个集合里时，向量那一步
        # 就已经混了 —— 后面加任何 SQL 条件都救不回来，因为 top-K 的名额已经被另一个
        # 变体占掉了。而且两套抽取规范产出的 statement 风格不同，混着召回本身有害。
        self.variant = "coding" if str(variant or "").strip().lower() == "coding" else "working"
        _suffix = "_cv" if self.variant == "coding" else ""

        # SQLite 路径
        if db_path is None:
            data_dir = os.path.dirname(config.history.db_path) if config.history.db_path else "./data"
            db_path = os.path.join(data_dir, "coding_memory.db")
        self._db_path = _variant_db_path(db_path, _suffix)
        # 原始轨迹是用户/agent 的上下文，不属于某个 variant。working 与 coding
        # 业务表继续分库，但 last-K 历史共用一份独立 SQLite，避免自动路由切换后失忆。
        self._trace_db_path = _variant_db_path(db_path, "_trace")

        # SQLite 连接（与现有 cache_sqlite 风格一致）
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._trace_conn: Optional[sqlite3.Connection] = None
        self._trace_lock = threading.Lock()

        # VDB：经验层与事实层各自独立 collection
        self._coding_config = _clone_config_with_collection(
            config, _derive_coding_collection_name, _suffix
        )
        self._fact_config = _clone_config_with_collection(
            config, _derive_fact_collection_name, _suffix
        )
        self._vec: Optional[VectorStoreBase] = None
        self._fact_vec: Optional[VectorStoreBase] = None

        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        # SQLite
        await _run_in_pool(self._init_sqlite_sync)
        await _run_in_pool(self._init_trace_sqlite_sync)
        # VDB
        self._vec = create_vector_store(self._coding_config)
        await self._vec.initialize()
        self._fact_vec = create_vector_store(self._fact_config)
        await self._fact_vec.initialize()
        self._initialized = True
        logger.info(
            f"[coding-store] initialized: variant={self.variant} sqlite={self._db_path} "
            f"vdb={self._coding_config.vector_store.collection_name} "
            f"facts={self._fact_config.vector_store.collection_name}"
        )

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

    async def close(self) -> None:
        for vec in (self._vec, self._fact_vec):
            if vec:
                try:
                    await vec.close()
                except Exception:
                    pass
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None
        with self._trace_lock:
            if self._trace_conn:
                self._trace_conn.close()
                self._trace_conn = None
        self._initialized = False

    # ------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------

    async def insert(self, memory: CodingMemory) -> None:
        """新增一条 coding memory（SQLite + VDB 全套）"""
        self._fill_timestamps(memory)
        await _run_in_pool(self._upsert_sql_sync, memory)
        await self._upsert_vec_keys(memory)
        logger.info(
            f"[coding-store] inserted experience_id={memory.experience_id} "
            f"task={memory.task!r} keys={1 + len(memory.search_keys)}"
        )

    async def update(self, memory: CodingMemory) -> None:
        """覆盖式更新一条 coding memory。"""
        # 拿旧 keys 长度，先删旧 VDB rows
        prev = await self.get_by_id(memory.experience_id, user_id=memory.user_id)
        if prev is None:
            raise ValueError("coding_memory_update_target_missing")
        if prev.agent_id != memory.agent_id or prev.workspace_id != memory.workspace_id:
            raise ValueError("coding_memory_update_authority_mismatch")
        prev_key_count = 1 + len(prev.search_keys)

        memory.updated_at = datetime.utcnow()
        if memory.created_at is None:
            memory.created_at = prev.created_at
        await _run_in_pool(self._upsert_sql_sync, memory)
        # 删旧 VDB
        await self._delete_vec_keys_by_count(memory.experience_id, prev_key_count)
        # 写新 VDB
        await self._upsert_vec_keys(memory)
        logger.info(
            f"[coding-store] updated experience_id={memory.experience_id} "
            f"old_keys={prev_key_count} new_keys={1 + len(memory.search_keys)}"
        )

    async def delete(self, experience_id: str, user_id: str) -> bool:
        """删除一条 coding memory，返回是否真有删到。"""
        prev = await self.get_by_id(experience_id, user_id=user_id)
        if prev is None:
            logger.info(f"[coding-store] delete skipped (not found): {experience_id}")
            return False
        await self._delete_vec_keys_by_count(experience_id, 1 + len(prev.search_keys))
        await _run_in_pool(self._delete_sql_sync, experience_id, user_id)
        logger.info(f"[coding-store] deleted experience_id={experience_id}")
        return True

    # ------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------

    async def get_by_id(
        self, experience_id: str, user_id: Optional[str] = None
    ) -> Optional[CodingMemory]:
        return await _run_in_pool(self._get_by_id_sync, experience_id, user_id)

    async def get_many(
        self, experience_ids: List[str], user_id: Optional[str] = None,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[CodingMemory]:
        if not experience_ids:
            return []
        return await _run_in_pool(
            self._get_many_sync, experience_ids, user_id,
            include_sources, exclude_sources,
        )

    async def list_user_tasks(
        self, user_id: str, limit: int = 50
    ) -> List[str]:
        """返回该 user 已有的 task 列表（最近修改的优先）。供 extractor prompt 引用。"""
        return await _run_in_pool(self._list_user_tasks_sync, user_id, limit)

    async def append_trace(
        self,
        messages: List[Any],
        *,
        request_id: str,
        user_id: str,
        agent_id: str = "default_agent",
        session_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        source: str = "default",
    ) -> None:
        """保存一次已限长的 working/coding 原始轨迹，供后续 last-K 上下文使用。"""
        payload = [m.to_dict() for m in messages]
        await _run_in_pool(
            self._append_trace_sync,
            request_id, user_id, agent_id, session_id,
            workspace_id, branch, source, payload,
        )

    async def get_recent_trace_messages(
        self,
        *,
        user_id: str,
        agent_id: str = "default_agent",
        max_user_turns: int = 5,
        source: str = "default",
    ) -> List[Any]:
        """取历史最近 K 个 user-turn；当前 write 尚未 append，因此不会自引用。"""
        from ..pipelines.base import ChatMessage
        rows = await _run_in_pool(
            self._get_recent_trace_payloads_sync,
            user_id, agent_id, source, max(1, max_user_turns) * 4,
        )
        messages: List[ChatMessage] = []
        for payload in rows:
            for item in payload:
                if isinstance(item, dict):
                    messages.append(ChatMessage.from_dict(item))
        if not messages:
            return []
        from .preproc import keep_last_k_user_turns, prepare_extractor_messages
        return keep_last_k_user_turns(
            prepare_extractor_messages(
                messages,
                tool_max_bytes=int(self.config.coding.tool_result_max_bytes or 2048),
            ),
            k=max(1, max_user_turns),
        )

    async def list_by_workspace(
        self,
        user_id: str,
        workspace_id: str,
        branch: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List["CodingMemory"]:
        """按 workspace_id（+可选 branch）纯过滤列出该 user 的完整 coding memory。

        作用域规则见 _scope_where（与 search 路径共用同一套）。无语义排序，按
        updated_at 倒序（最近修改优先）。
        """
        return await _run_in_pool(
            self._list_by_workspace_sync, user_id, workspace_id, branch, limit, offset
        )

    def _list_by_workspace_sync(
        self, user_id: str, workspace_id: str, branch: Optional[str],
        limit: int, offset: int,
    ) -> List["CodingMemory"]:
        scope_sql, scope_params = _scope_where(workspace_id, branch)
        with self._lock:
            assert self._conn is not None
            sql = (f"SELECT * FROM coding_memory_meta WHERE user_id = ? AND {scope_sql} "
                   f"ORDER BY updated_at DESC LIMIT ? OFFSET ?")
            params: List[Any] = [user_id] + scope_params + [limit, offset]
            cur = self._conn.execute(sql, params)
            return [_row_to_memory(r) for r in cur.fetchall()]

    # ------------------------------------------------------------
    # 召回
    # ------------------------------------------------------------

    async def search_by_query_embedding(
        self,
        query_embedding: List[float],
        user_id: str,
        *,
        agent_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        top: int = 40,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        向量检索。返回 dedup 后按分数降序的 hit list：
            [
                {
                    "experience_id": "...",
                    "score": 0.83,
                    "matched_key_kind": "task" | "aspect",
                    "matched_key_text": "...",
                },
                ...
            ]
        agent_id 是写授权作用域，传入时精确匹配；workspace_id / branch 的规则见
        _scope_where。不传任何作用域则维持纯语义召回；传了则只保留 SQLite 里落在
        作用域内的经验，并按选择率放大向量取数，避免"先取 top-N 再过滤"把召回吃掉。
        """
        if self._vec is None:
            return []

        allowed: Optional[Set[str]] = None
        if agent_id or workspace_id:
            allowed, total = await _run_in_pool(
                self._authority_scoped_experience_ids_sync,
                user_id,
                agent_id,
                workspace_id,
                branch,
            )
            if not allowed:
                logger.info(
                    f"[coding-store] no experience in scope: "
                    f"agent_id={agent_id!r} workspace_id={workspace_id!r} branch={branch!r} "
                    f"(user has {total}); skip vdb search"
                )
                return []
            top = _scope_overfetch(top, len(allowed), total)

        raw = await self._vec.search(
            query_embedding=query_embedding,
            user_id=user_id,
            limit=top,
            score_threshold=0.0,
            only_latest=True,
            include_sources=include_sources,
            exclude_sources=exclude_sources,
        )

        best: Dict[str, Dict[str, Any]] = {}
        for hit in raw:
            node: MemoryNode = hit.get("node")
            score: float = hit.get("score", 0.0)
            if node is None:
                continue
            custom = node.custom or {}
            eid = custom.get("coding_experience_id")
            if not eid:
                continue
            # 认 SQLite 那份而不是向量行 payload 里的 coding_workspace_id 副本：
            # 两边不是一个事务写的，经验换 workspace 后 payload 可能还是旧值。
            if allowed is not None and eid not in allowed:
                continue
            # dedup by experience_id, 保留最高分
            cur = best.get(eid)
            if cur is None or score > cur["score"]:
                best[eid] = {
                    "experience_id": eid,
                    "score": score,
                    "matched_key_kind": custom.get("coding_key_kind", "aspect"),
                    "matched_key_text": custom.get("coding_key_text") or node.content,
                }
        # 排序
        return sorted(best.values(), key=lambda h: -h["score"])

    # ------------------------------------------------------------
    # 事实层
    # ------------------------------------------------------------

    async def insert_fact(self, fact: WorkingFact) -> None:
        """新增一条事实（SQLite + 事实 collection 各一行）。"""
        if fact.created_at is None:
            fact.created_at = datetime.utcnow()
        await _run_in_pool(self._upsert_fact_sql_sync, fact)
        await self._upsert_fact_vec(fact)
        logger.info(
            f"[coding-store] inserted fact_id={fact.fact_id} "
            f"statement={fact.statement!r}"
        )

    async def get_fact(
        self, fact_id: str, user_id: Optional[str] = None
    ) -> Optional[WorkingFact]:
        facts = await self.get_facts([fact_id], user_id=user_id)
        return facts[0] if facts else None

    async def get_facts(
        self, fact_ids: List[str], user_id: Optional[str] = None
    ) -> List[WorkingFact]:
        if not fact_ids:
            return []
        return await _run_in_pool(self._get_facts_sync, fact_ids, user_id)

    async def search_facts(
        self,
        query_embedding: List[float],
        user_id: str,
        *,
        agent_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        top: int = 5,
        query_text: Optional[str] = None,
        min_score: float = 0.0,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[Tuple[WorkingFact, float]]:
        """事实层语义召回，只返回 active，按分数降序，最多 top 条。

        SUPERSEDE 时向量行已被删掉，理论上召不到 superseded；这里再按 SQLite 的
        status 过一道，避免两边不一致时把过期结论放出去。

        agent_id 是写授权作用域，传入时精确匹配；workspace_id / branch 的规则见
        _scope_where。不传任何作用域则不过滤，传了按 SQLite 里的值筛，并按选择率
        放大向量取数。

        向量库按 top 的 4 倍过采样：向量行和 SQLite 行不是一个事务里写的，中间挂掉
        就会留下"向量还在、SQLite 没了"的孤儿，软删时删向量失败也会留下 superseded
        残留。这些都会在下面被过滤掉，不过采样的话它们会直接吃掉候选名额——极端情况
        下前 top 条全是残留，召回就空了。

        给了 query_text 就在截断到 top 之前加一路 BM25 词面召回做融合重排（见
        coding/lexical.py）。返回的分数始终是稠密余弦分，不是融合分——上层
        client.py:851 用 0.30 的余弦阈值过闸门，返回融合分（另一套量纲）会把事实层
        静默清空。min_score 让调用方把那道闸门下移到重排之前：先砍掉过不了闸的候选再
        重排，返回条数就恒等于纯稠密的条数，词面只改选谁、不会让召回变少。
        """
        if self._fact_vec is None:
            return []

        fetch = top * 4
        if agent_id or workspace_id:
            scoped, total = await _run_in_pool(
                self._count_active_facts_sync,
                user_id,
                agent_id,
                workspace_id,
                branch,
            )
            if not scoped:
                logger.info(
                    f"[coding-store] no active fact in scope: "
                    f"agent_id={agent_id!r} workspace_id={workspace_id!r} branch={branch!r} "
                    f"(user has {total}); skip fact vdb search"
                )
                return []
            fetch = _scope_overfetch(fetch, scoped, total)

        raw = await self._fact_vec.search(
            query_embedding=query_embedding,
            user_id=user_id,
            limit=fetch,
            score_threshold=0.0,
            only_latest=True,
            include_sources=include_sources,
            exclude_sources=exclude_sources,
        )

        scores: Dict[str, float] = {}
        for hit in raw:
            node: MemoryNode = hit.get("node")
            if node is None:
                continue
            fid = (node.custom or {}).get("working_fact_id")
            if not fid:
                continue
            score = hit.get("score", 0.0)
            if fid not in scores or score > scores[fid]:
                scores[fid] = score

        if not scores:
            return []

        ordered_ids = sorted(scores, key=lambda f: -scores[f])
        facts = await self.get_facts(ordered_ids, user_id=user_id)
        alive = [
            (f, scores[f.fact_id]) for f in facts
            if f.status == "active"
            and (not agent_id or f.agent_id == agent_id)
            and _scope_matches(f.workspace_id, f.branch, workspace_id, branch)
        ]
        kept = [(f, s) for f, s in alive if s >= min_score]
        # get_facts 不保证按 ordered_ids 的顺序返回，这里显式按分数重排。
        kept.sort(key=lambda p: -p[1])
        if alive and not kept:
            # 闸门在这里就把事实层清空了。调用方（client.py）随后只看到空列表，分不清
            # "没召回到"和"召回了但都不够分"，而后者意味着阈值调过头了。所以在拿得到
            # 分数的地方报。
            logger.warning(
                f"[coding-store] fact layer emptied by min_score={min_score}: "
                f"{len(alive)} active hit(s), top={max(s for _, s in alive):.3f}"
            )
        if len(kept) <= top or not (query_text or "").strip():
            return kept[:top]

        # 候选比名额多，词面通道才有得选。文本要和嵌入侧一致（_upsert_fact_vec 嵌的是
        # `subject: statement`），否则两路排的不是同一个东西。
        from .lexical import rerank

        by_id = {f.fact_id: (f, s) for f, s in kept}
        order = rerank(
            query_text,
            [(f.fact_id, s, f"{f.subject}: {f.statement}") for f, s in kept],
        )
        if order is None:
            return kept[:top]
        fused = [by_id[fid] for fid in order if fid in by_id][:top]
        dense_top = {f.fact_id for f, _ in kept[:top]}
        swapped = [f.fact_id for f, _ in fused if f.fact_id not in dense_top]
        if swapped:
            logger.info(
                f"[coding-store] bm25 rerank changed the top-{top}: "
                f"{len(swapped)} fact(s) swapped in from {len(kept)} candidates"
            )
        # 返回稠密分（见 docstring）；顺序已按融合排好，不要再按分数排。
        return fused

    async def get_fact_ancestors(
        self, fact_id: str, *, user_id: Optional[str] = None, max_depth: int = 8
    ) -> List[WorkingFact]:
        """顺 fact_link 反向走，返回这条事实的历代前身，时间正序，不含自己。

        SUPERSEDE 只软删不真删，被顶掉的那条原样留在库里、fact_link 指向后继，
        所以"这个结论以前说过什么"不需要另存一份，反查即可。

        最多走 max_depth 代；到头、断链或成环都停。成环理论上不该发生，但一旦
        发生就是死循环，不值得为一个总结冒这个险。
        """
        return await _run_in_pool(
            self._get_fact_ancestors_sync, fact_id, user_id, max_depth
        )

    async def supersede_fact(self, old_fact_id: str, new_fact_id: str) -> bool:
        """旧事实软删：status=superseded + fact_link 指向新事实，并撤出向量库。

        SQLite 行保留，fact_link 链路可回溯；向量行删掉，不再占召回名额。
        """
        changed = await _run_in_pool(
            self._supersede_fact_sql_sync, old_fact_id, new_fact_id
        )
        if not changed:
            return False
        if self._fact_vec is not None:
            try:
                await self._fact_vec.delete(old_fact_id)
            except Exception as e:
                logger.debug(f"[coding-store] delete fact vec {old_fact_id} ignored: {e}")
        logger.info(
            f"[coding-store] superseded fact_id={old_fact_id} by={new_fact_id}"
        )
        return True

    async def set_fact_derived_experiences(
        self, fact_id: str, experience_ids: List[str]
    ) -> None:
        """覆盖写 fact.derived_experience_ids（调用方负责取并集）。"""
        await _run_in_pool(
            self._set_json_column_sync,
            "working_fact", "fact_id", fact_id,
            "derived_experience_ids", experience_ids,
        )

    async def set_experience_source_facts(
        self, experience_id: str, fact_ids: List[str]
    ) -> None:
        """覆盖写 experience.source_fact_ids（调用方负责取并集）。"""
        await _run_in_pool(
            self._set_json_column_sync,
            "coding_memory_meta", "experience_id", experience_id,
            "source_fact_ids", fact_ids,
        )

    # ------------------------------------------------------------
    # SQLite 同步实现
    # ------------------------------------------------------------

    def _fill_timestamps(self, memory: CodingMemory) -> None:
        now = datetime.utcnow()
        if memory.created_at is None:
            memory.created_at = now
        memory.updated_at = now

    def _upsert_sql_sync(self, memory: CodingMemory) -> None:
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                """
                INSERT INTO coding_memory_meta (
                    experience_id, user_id, agent_id, task, search_keys, solution,
                    boundary_envs, workspace_id, branch, session_id,
                    files, source_fact_ids, confidence, source, extraction_source,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(experience_id) DO UPDATE SET
                    user_id         = excluded.user_id,
                    agent_id        = excluded.agent_id,
                    task            = excluded.task,
                    search_keys     = excluded.search_keys,
                    solution        = excluded.solution,
                    boundary_envs   = excluded.boundary_envs,
                    workspace_id    = excluded.workspace_id,
                    branch          = excluded.branch,
                    session_id      = excluded.session_id,
                    files           = excluded.files,
                    source_fact_ids = excluded.source_fact_ids,
                    confidence      = excluded.confidence,
                    source          = excluded.source,
                    extraction_source = excluded.extraction_source,
                    updated_at      = excluded.updated_at
                """,
                (
                    memory.experience_id, memory.user_id, memory.agent_id,
                    memory.task, json.dumps(memory.search_keys, ensure_ascii=False),
                    memory.solution, memory.boundary_envs,
                    memory.workspace_id, memory.branch, memory.session_id,
                    json.dumps(memory.files, ensure_ascii=False),
                    json.dumps(memory.source_fact_ids, ensure_ascii=False),
                    memory.confidence, memory.source, memory.extraction_source,
                    (memory.created_at or datetime.utcnow()).isoformat(),
                    (memory.updated_at or datetime.utcnow()).isoformat(),
                ),
            )
    def _delete_sql_sync(self, experience_id: str, user_id: str) -> None:
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                "DELETE FROM coding_memory_meta WHERE experience_id = ? AND user_id = ?",
                (experience_id, user_id),
            )

    def _get_by_id_sync(
        self, experience_id: str, user_id: Optional[str]
    ) -> Optional[CodingMemory]:
        with self._lock:
            assert self._conn is not None
            if user_id:
                cur = self._conn.execute(
                    "SELECT * FROM coding_memory_meta WHERE experience_id = ? AND user_id = ?",
                    (experience_id, user_id),
                )
            else:
                cur = self._conn.execute(
                    "SELECT * FROM coding_memory_meta WHERE experience_id = ?",
                    (experience_id,),
                )
            row = cur.fetchone()
            return _row_to_memory(row) if row else None

    def _get_many_sync(
        self, experience_ids: List[str], user_id: Optional[str],
        include_sources: Optional[List[str]], exclude_sources: Optional[List[str]],
    ) -> List[CodingMemory]:
        if not experience_ids:
            return []
        with self._lock:
            assert self._conn is not None
            placeholders = ",".join("?" * len(experience_ids))
            if user_id:
                sql = f"SELECT * FROM coding_memory_meta WHERE experience_id IN ({placeholders}) AND user_id = ?"
                params = list(experience_ids) + [user_id]
            else:
                sql = f"SELECT * FROM coding_memory_meta WHERE experience_id IN ({placeholders})"
                params = list(experience_ids)
            if include_sources:
                marks = ",".join("?" * len(include_sources))
                sql += f" AND source IN ({marks})"
                params.extend(include_sources)
            if exclude_sources:
                marks = ",".join("?" * len(exclude_sources))
                sql += f" AND source NOT IN ({marks})"
                params.extend(exclude_sources)
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
            # 保留输入顺序
            by_id = {r["experience_id"]: _row_to_memory(r) for r in rows}
            return [by_id[eid] for eid in experience_ids if eid in by_id]

    def _authority_scoped_experience_ids_sync(
        self,
        user_id: str,
        agent_id: Optional[str],
        workspace_id: Optional[str],
        branch: Optional[str],
    ) -> Tuple[Set[str], int]:
        """返回 (落在授权作用域内的 experience_id 集合, 该 user 的经验总数)。

        一次查询同时拿到过滤结果和选择率分母：前者供向量命中做交集，后者定放大倍数。
        """
        scope_sql, scope_params = _scope_where(workspace_id, branch)
        if agent_id:
            scope_sql = f"agent_id = ? AND ({scope_sql})"
            scope_params = [agent_id, *scope_params]
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                f"SELECT experience_id, ({scope_sql}) AS in_scope "
                f"FROM coding_memory_meta WHERE user_id = ?",
                scope_params + [user_id],
            )
            rows = cur.fetchall()
        return {r["experience_id"] for r in rows if r["in_scope"]}, len(rows)

    def _count_active_facts_sync(
        self,
        user_id: str,
        agent_id: Optional[str],
        workspace_id: Optional[str],
        branch: Optional[str],
    ) -> Tuple[int, int]:
        """返回 (作用域内 active 事实数, 该 user 的 active 事实总数)。

        事实层的过滤在 get_facts 拿到对象之后用 _scope_matches 做，这里只要选择率。
        """
        scope_sql, scope_params = _scope_where(workspace_id, branch)
        if agent_id:
            scope_sql = f"agent_id = ? AND ({scope_sql})"
            scope_params = [agent_id, *scope_params]
        with self._lock:
            assert self._conn is not None
            row = self._conn.execute(
                f"SELECT COUNT(*) AS total, "
                f"SUM(CASE WHEN {scope_sql} THEN 1 ELSE 0 END) AS scoped "
                f"FROM working_fact WHERE user_id = ? AND status = 'active'",
                scope_params + [user_id],
            ).fetchone()
        return int(row["scoped"] or 0), int(row["total"] or 0)

    def _upsert_fact_sql_sync(self, fact: WorkingFact) -> None:
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                """
                INSERT INTO working_fact (
                    fact_id, user_id, agent_id, session_id, workspace_id, branch,
                    source, statement, files, subject, details,
                    fact_link, derived_experience_ids,
                    status, fact_chain_summary, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fact_id) DO UPDATE SET
                    user_id                = excluded.user_id,
                    agent_id               = excluded.agent_id,
                    session_id             = excluded.session_id,
                    workspace_id           = excluded.workspace_id,
                    branch                 = excluded.branch,
                    source                 = excluded.source,
                    statement              = excluded.statement,
                    files                  = excluded.files,
                    subject                = excluded.subject,
                    details                = excluded.details,
                    fact_link              = excluded.fact_link,
                    derived_experience_ids = excluded.derived_experience_ids,
                    status                 = excluded.status,
                    fact_chain_summary     = excluded.fact_chain_summary
                """,
                (
                    fact.fact_id, fact.user_id, fact.agent_id,
                    fact.session_id, fact.workspace_id, fact.branch, fact.source,
                    fact.statement,
                    json.dumps(fact.files, ensure_ascii=False),
                    fact.subject or "",
                    fact.details or "",
                    fact.fact_link,
                    json.dumps(fact.derived_experience_ids, ensure_ascii=False),
                    fact.status,
                    fact.fact_chain_summary or "",
                    (fact.created_at or datetime.utcnow()).isoformat(),
                ),
            )

    def _get_facts_sync(
        self, fact_ids: List[str], user_id: Optional[str]
    ) -> List[WorkingFact]:
        with self._lock:
            assert self._conn is not None
            placeholders = ",".join("?" * len(fact_ids))
            sql = f"SELECT * FROM working_fact WHERE fact_id IN ({placeholders})"
            params: List[Any] = list(fact_ids)
            if user_id:
                sql += " AND user_id = ?"
                params.append(user_id)
            cur = self._conn.execute(sql, params)
            by_id = {r["fact_id"]: _row_to_fact(r) for r in cur.fetchall()}
            # 保留输入顺序
            return [by_id[fid] for fid in fact_ids if fid in by_id]

    def _get_fact_ancestors_sync(
        self, fact_id: str, user_id: Optional[str], max_depth: int
    ) -> List[WorkingFact]:
        with self._lock:
            assert self._conn is not None
            sql = "SELECT * FROM working_fact WHERE fact_link = ?"
            if user_id:
                sql += " AND user_id = ?"
            # 同一条被顶掉两次不该发生（supersede 只对 active 生效），真撞上就取最早的
            sql += " ORDER BY created_at ASC LIMIT 1"

            out: List[WorkingFact] = []
            seen = {fact_id}
            cur_id = fact_id
            for _ in range(max_depth):
                params = [cur_id] + ([user_id] if user_id else [])
                row = self._conn.execute(sql, params).fetchone()
                if row is None:
                    break
                parent = _row_to_fact(row)
                if parent.fact_id in seen:
                    logger.warning(
                        f"[coding-store] fact_link cycle at {parent.fact_id}, stop walking"
                    )
                    break
                seen.add(parent.fact_id)
                out.append(parent)
                cur_id = parent.fact_id
            out.reverse()          # 反查是由近及远，调用方要时间正序
            return out

    def _supersede_fact_sql_sync(self, old_fact_id: str, new_fact_id: str) -> bool:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "UPDATE working_fact SET status = 'superseded', fact_link = ? "
                "WHERE fact_id = ? AND status = 'active'",
                (new_fact_id, old_fact_id),
            )
            return cur.rowcount > 0

    def _set_json_column_sync(
        self, table: str, key_column: str, key: str, column: str, value: List[str]
    ) -> None:
        with self._lock:
            assert self._conn is not None
            self._conn.execute(
                f"UPDATE {table} SET {column} = ? WHERE {key_column} = ?",
                (json.dumps(value, ensure_ascii=False), key),
            )

    def _list_user_tasks_sync(self, user_id: str, limit: int) -> List[str]:
        with self._lock:
            assert self._conn is not None
            cur = self._conn.execute(
                "SELECT task FROM coding_memory_meta WHERE user_id = ? "
                "ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            )
            return [r["task"] for r in cur.fetchall()]

    def _append_trace_sync(
        self,
        request_id: str,
        user_id: str,
        agent_id: str,
        session_id: Optional[str],
        workspace_id: Optional[str],
        branch: Optional[str],
        source: str,
        payload: List[Dict[str, Any]],
    ) -> None:
        with self._trace_lock:
            assert self._trace_conn is not None
            self._trace_conn.execute(
                "INSERT OR IGNORE INTO coding_trace "
                "(request_id,user_id,agent_id,session_id,workspace_id,branch,source,messages_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    request_id, user_id, agent_id or "default_agent", session_id,
                    workspace_id, branch, source,
                    json.dumps(payload, ensure_ascii=False), _now_iso(),
                ),
            )
            # last-K 只需近期轨迹；保留 100 次 write 足够覆盖异常大的单-turn 分布，
            # 同时避免原始工具轨迹在 SQLite 中无限增长。
            self._trace_conn.execute(
                "DELETE FROM coding_trace WHERE rowid IN ("
                "SELECT rowid FROM coding_trace WHERE user_id=? AND agent_id=? "
                "ORDER BY created_at DESC LIMIT -1 OFFSET 100)",
                (user_id, agent_id or "default_agent"),
            )

    def _get_recent_trace_payloads_sync(
        self, user_id: str, agent_id: str, source: str, limit: int,
    ) -> List[List[Dict[str, Any]]]:
        with self._trace_lock:
            assert self._trace_conn is not None
            rows = self._trace_conn.execute(
                "SELECT messages_json FROM coding_trace "
                "WHERE user_id=? AND agent_id=? AND source=? "
                "ORDER BY created_at DESC LIMIT ?",
                (user_id, agent_id or "default_agent", source, max(1, limit)),
            ).fetchall()
        out: List[List[Dict[str, Any]]] = []
        for row in reversed(rows):
            try:
                payload = json.loads(row["messages_json"] or "[]")
            except Exception:
                payload = []
            if isinstance(payload, list):
                out.append(payload)
        return out

    # ------------------------------------------------------------
    # VDB 实现
    # ------------------------------------------------------------

    async def _upsert_vec_keys(self, memory: CodingMemory) -> None:
        """把 task + search_keys 写入 VDB（每行一个 MemoryNode）。"""
        if self._vec is None:
            return

        all_keys: List[Tuple[str, str]] = [("task", memory.task)]
        for k in memory.search_keys:
            if k:
                all_keys.append(("aspect", k))
        if not all_keys:
            return

        # batch embed
        texts = [t for _, t in all_keys]
        try:
            embeddings = await self.embed_service.embed_batch(texts)
        except Exception as e:
            logger.warning(f"[coding-store] embed_batch failed: {e}")
            return

        nodes: List[MemoryNode] = []
        for i, ((kind, text), emb) in enumerate(zip(all_keys, embeddings)):
            node = MemoryNode(
                node_id=_vec_node_id(memory.experience_id, i),
                user_id=memory.user_id,
                agent_id=memory.agent_id,
                session_id=memory.session_id or "",
                source=memory.source,
                # layer 字段不参与 chat 路径过滤（独立 collection），用 L1_RAW 占位
                layer=MemoryLayer.L1_RAW,
                content=text,
                embedding=emb,
                status=MemoryStatus.ACTIVE,
                custom={
                    "coding_experience_id": memory.experience_id,
                    "coding_key_kind": kind,
                    "coding_key_text": text,
                    "coding_workspace_id": memory.workspace_id or "",
                    "coding_branch": memory.branch or "",
                    "coding_kind_marker": "coding_key",
                },
            )
            nodes.append(node)

        try:
            await self._vec.upsert_batch(nodes)
        except Exception as e:
            logger.warning(f"[coding-store] vdb upsert_batch failed: {e}")

    async def _upsert_fact_vec(self, fact: WorkingFact) -> None:
        """事实入向量库。一条事实一行，node_id 就是 fact_id。

        检索文本是 statement，前面缀上 subject（有的话）。subject 通常已经在
        statement 里出现过，但点查类问题（"Dylan 第二天午餐吃什么"）成败全看主体
        这个词的权重，缀一遍把它从"顺带提到"提成"标题"。details 刻意不进向量：
        它是原文，长度不受控，embed 进去会把摘要的语义冲淡，而它要解决的问题是
        "命中之后读得到值"，不是"更容易命中"。
        """
        if self._fact_vec is None or not fact.statement:
            return
        vector_text = (
            f"{fact.subject}: {fact.statement}" if fact.subject else fact.statement
        )
        try:
            embedding = await self.embed_service.embed(vector_text)
        except Exception as e:
            logger.warning(f"[coding-store] fact embed failed: {e}")
            return

        node = MemoryNode(
            node_id=fact.fact_id,
            user_id=fact.user_id,
            agent_id=fact.agent_id,
            session_id=fact.session_id or "",
            source=fact.source,
            # layer 不参与 chat 路径过滤（独立 collection），用 L1_RAW 占位
            layer=MemoryLayer.L1_RAW,
            content=vector_text,
            embedding=embedding,
            status=MemoryStatus.ACTIVE,
            custom={
                "working_fact_id": fact.fact_id,
                "coding_workspace_id": fact.workspace_id or "",
                "coding_branch": fact.branch or "",
                "coding_kind_marker": "working_fact",
                "coding_fact_subject": fact.subject or "",
            },
        )
        try:
            await self._fact_vec.upsert_batch([node])
        except Exception as e:
            logger.warning(f"[coding-store] fact vdb upsert failed: {e}")

    async def _delete_vec_keys_by_count(self, experience_id: str, count: int) -> None:
        """按 deterministic node_id 删除 0..count-1。"""
        if self._vec is None or count <= 0:
            return
        for i in range(count):
            try:
                await self._vec.delete(_vec_node_id(experience_id, i))
            except Exception as e:
                logger.debug(
                    f"[coding-store] delete vdb {_vec_node_id(experience_id, i)} ignored: {e}"
                )
