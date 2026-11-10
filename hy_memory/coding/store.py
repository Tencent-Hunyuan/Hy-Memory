# -*- coding: utf-8 -*-
"""
Working / Coding 双层存储：业务仓储保存经验、事实和轨迹，VDB 保存检索向量。

业务仓储默认 SQLite，可配置 PostgreSQL；向量集合与检索行为保持独立。
写入先生成 embedding，再写业务仓储，最后写向量。两层不是同一个事务，
数据库切换不会自动获得跨存储原子性。对外使用见 docs/scene_coding_usage.md。
"""

import concurrent.futures
import functools
import logging
import math
import os
from copy import deepcopy
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from ..config import MemoryConfig
from ..core.embed_service import EmbedService
from ..data.vector_store import create_vector_store
from ..data.vector_store_base import VectorStoreBase
from ..models.memory import MemoryLayer, MemoryNode, MemoryStatus
from ..utils.async_executor import run_in_executor_guarded
from .types import CodingMemory, CodingWriteError, WorkingFact
from .repository import create_memory_repository, memory_repository_exists
from .relational_repository import _to_epoch, _scope_where as _scope_where
from .repository_schema import (
    _CREATE_TABLE_SQL as _CREATE_TABLE_SQL,
    _CREATE_FACT_TABLE_SQL as _CREATE_FACT_TABLE_SQL,
)
from .repository_base import MemoryRepository

logger = logging.getLogger(__name__)


# ================================================================
# 业务存储线程池
# ================================================================

_CODING_STORAGE_POOL_SIZE = 16
_coding_storage_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=_CODING_STORAGE_POOL_SIZE,
    thread_name_prefix="coding-storage",
)


async def _run_in_pool(func, *args, **kwargs):
    call = functools.partial(func, *args, **kwargs) if args or kwargs else func
    return await run_in_executor_guarded(_coding_storage_executor, call)


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
# "只有部分后端才真生效"的硬过滤比没有更危险。所以过滤放在业务库 —— 它才是这两
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


def _vec_node_id(experience_id: str, key_idx: int) -> str:
    return f"{experience_id}:{key_idx}"


def _later(a: Optional[datetime], b: Optional[datetime]) -> Optional[datetime]:
    """两个对话时间取较晚的；一边缺就取另一边。"""
    if a is None:
        return b
    if b is None:
        return a
    return a if _to_epoch(a) >= _to_epoch(b) else b


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
        repository: Optional[MemoryRepository] = None,
    ):
        self.config = config
        self.embed_service = embed_service
        # 变体后缀。working → ""（路径和集合名与改动前逐字相同，已落盘的库照旧能读）；
        # coding → "_cv"（独立 sqlite + 独立两个向量集合）。
        #
        # 为什么必须分库而不是加一个 variant 列做过滤：检索是**先**在向量集合里取
        # top-K、**再**用权威业务库过滤。两个变体的 fact 混在同一个集合里时，向量那一步
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

        self._repository = repository if repository is not None else create_memory_repository(
            config, self._db_path, self._trace_db_path, self.variant
        )

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

    @staticmethod
    def database_path(
        config: MemoryConfig,
        db_path: Optional[str] = None,
        variant: str = "working",
    ) -> str:
        """Return the authoritative SQLite path without creating anything."""
        if db_path is None:
            data_dir = (
                os.path.dirname(config.history.db_path)
                if config.history.db_path else "./data"
            )
            db_path = os.path.join(data_dir, "coding_memory.db")
        normalized = (
            "coding"
            if str(variant or "").strip().lower() == "coding"
            else "working"
        )
        return _variant_db_path(db_path, "_cv" if normalized == "coding" else "")

    @classmethod
    def database_exists(
        cls,
        config: MemoryConfig,
        db_path: Optional[str] = None,
        variant: str = "working",
    ) -> bool:
        """Whether a business namespace exists, without creating storage."""
        return memory_repository_exists(
            config, cls.database_path(config, db_path, variant), variant
        )

    @property
    def _conn(self):
        return self._repository._conn

    @_conn.setter
    def _conn(self, value):
        # MemHub's pinned offline migration constructs the facade with __new__.
        if not hasattr(self, "_repository"):
            from .repository_sqlite import SqliteMemoryRepository
            self._repository = SqliteMemoryRepository(self._db_path, self._db_path)
        self._repository._conn = value

    @property
    def _trace_conn(self):
        return self._repository._trace_conn

    @property
    def _lock(self):
        return self._repository._lock

    @property
    def _trace_lock(self):
        return self._repository._trace_lock

    def _init_sqlite_sync(self) -> None:
        """Compatibility hook for the pinned MemHub offline SQLite migration."""
        self._repository._init_sqlite_sync()

    async def initialize(self) -> None:
        if self._initialized:
            return
        try:
            await _run_in_pool(self._repository.initialize)
            # VDB
            self._vec = create_vector_store(self._coding_config)
            await self._vec.initialize()
            self._fact_vec = create_vector_store(self._fact_config)
            await self._fact_vec.initialize()
        except BaseException:
            await self.close()
            raise
        self._initialized = True
        logger.info(
            f"[coding-store] initialized: variant={self.variant} backend={self.config.business_store.backend} "
            f"vdb={self._coding_config.vector_store.collection_name} "
            f"facts={self._fact_config.vector_store.collection_name}"
        )

    async def close(self) -> None:
        for vec in (self._vec, self._fact_vec):
            if vec:
                try:
                    await vec.close()
                except Exception:
                    pass
        await _run_in_pool(self._repository.close)
        self._initialized = False

    # ------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------

    async def insert(self, memory: CodingMemory) -> None:
        """新增一条 coding memory（业务库 + VDB 全套）。

        先向量化再落业务库：向量化失败抛 CodingWriteError(EMBEDDING_FAILED)，
        什么都不写，不留一条没有向量、永远检索不到的经验。
        """
        self._fill_timestamps(memory)
        embeddings = await self._embed_vec_keys(memory)
        await _run_in_pool(self._repository.upsert_experience, memory)
        await self._upsert_vec_keys(memory, embeddings=embeddings)
        logger.info(
            f"[coding-store] inserted experience_id={memory.experience_id} "
            f"keys={1 + len(memory.search_keys)}"
        )
        logger.debug(
            f"[coding-store] inserted experience_id={memory.experience_id} "
            f"task={memory.task!r}"
        )

    async def update(self, memory: CodingMemory) -> None:
        """覆盖式更新一条 coding memory。"""
        # 拿旧 keys 长度，先删旧 VDB rows
        prev = await self.get_by_id(memory.experience_id, user_id=memory.user_id)
        if prev is None:
            raise ValueError("coding_memory_update_target_missing")
        if (
            not prev.session_id
            or not memory.session_id
            or prev.agent_id != memory.agent_id
            or prev.session_id != memory.session_id
            or prev.workspace_id != memory.workspace_id
        ):
            raise ValueError("coding_memory_update_authority_mismatch")
        prev_key_count = 1 + len(prev.search_keys)
        # 先向量化：失败时旧经验（业务库与向量行）原样保留，不改一个字。
        embeddings = await self._embed_vec_keys(memory)

        memory.updated_at = datetime.utcnow()
        if memory.created_at is None:
            memory.created_at = prev.created_at
        # 合并后的经验讲的是两段对话，对话时间取较晚的那段
        memory.memory_at = _later(prev.memory_at, memory.memory_at)
        await _run_in_pool(self._repository.upsert_experience, memory)
        # 删旧 VDB
        await self._delete_vec_keys_by_count(memory.experience_id, prev_key_count)
        # 写新 VDB
        await self._upsert_vec_keys(memory, embeddings=embeddings)
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
        await _run_in_pool(self._repository.delete_experience, experience_id, user_id)
        logger.info(f"[coding-store] deleted experience_id={experience_id}")
        return True

    async def edit_experience(
        self,
        experience_id: str,
        *,
        user_id: Optional[str] = None,
        task: Optional[str] = None,
        solution: Optional[str] = None,
        search_keys: Optional[List[str]] = None,
    ) -> Optional[CodingMemory]:
        """调用方手动改一条经验（client.update 用）。返回改后的经验；不存在返回 None。

        与 update() 的区别：update 是写入链路的覆盖式重写，带写授权校验（同
        agent/session/workspace 才能改）；这里是用户对已有条目的直接编辑，只动
        给定的字段，其余原样保留，因此不做授权校验。

        向量行只嵌 task + search_keys（solution 不进向量），所以只改 solution 时
        不重嵌、不碰向量库；改了 task / search_keys 才删旧 key 行重写。
        updated_at 总是刷新。
        """
        prev = await self.get_by_id(experience_id, user_id=user_id)
        if prev is None:
            return None
        updated = deepcopy(prev)
        if task is not None:
            updated.task = task
        if solution is not None:
            updated.solution = solution
        if search_keys is not None:
            updated.search_keys = list(search_keys)
        updated.updated_at = datetime.utcnow()
        await _run_in_pool(self._repository.upsert_experience, updated)
        if updated.task != prev.task or updated.search_keys != prev.search_keys:
            await self._delete_vec_keys_by_count(experience_id, 1 + len(prev.search_keys))
            await self._upsert_vec_keys(updated)
        logger.info(f"[coding-store] edited experience_id={experience_id}")
        return updated

    async def edit_fact(
        self,
        fact_id: str,
        *,
        statement: str,
        user_id: Optional[str] = None,
    ) -> Optional[WorkingFact]:
        """调用方手动改一条事实的 statement。返回改后的事实；不存在返回 None。

        statement 是事实层的检索文本，所以 active 事实会重嵌并覆盖它那一行向量
        （node_id 就是 fact_id，upsert 即覆盖）。superseded 事实的向量行早已撤掉，
        这里不把它加回去，否则会让过期结论重新占召回名额。
        """
        prev = await self.get_fact(fact_id, user_id=user_id)
        if prev is None:
            return None
        updated = deepcopy(prev)
        updated.statement = statement
        await _run_in_pool(self._repository.upsert_fact, updated)
        if updated.status == "active":
            await self._upsert_fact_vec(updated)
        logger.info(f"[coding-store] edited fact_id={fact_id}")
        return updated

    async def delete_fact(self, fact_id: str, user_id: Optional[str] = None) -> bool:
        """物理删除一条事实（业务库行 + 事实向量行），返回是否真有删到。

        别处留下的引用（经验的 source_fact_ids、后继事实的 fact_link）不清：
        召回侧本来就按存在性过滤死链（get_many / get_facts 查不到自然掉队）。
        """
        prev = await self.get_fact(fact_id, user_id=user_id)
        if prev is None:
            logger.info(f"[coding-store] delete fact skipped (not found): {fact_id}")
            return False
        if self._fact_vec is not None:
            try:
                await self._fact_vec.delete(fact_id)
            except Exception as e:
                logger.debug(f"[coding-store] delete fact vec {fact_id} ignored: {e}")
        await _run_in_pool(self._repository.delete_fact, fact_id, prev.user_id)
        logger.info(f"[coding-store] deleted fact_id={fact_id}")
        return True

    # ------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------

    async def get_by_id(
        self, experience_id: str, user_id: Optional[str] = None
    ) -> Optional[CodingMemory]:
        return await _run_in_pool(self._repository.get_experience, experience_id, user_id)

    async def get_many(
        self, experience_ids: List[str], user_id: Optional[str] = None,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List[CodingMemory]:
        if not experience_ids:
            return []
        return await _run_in_pool(
            self._repository.get_experiences, experience_ids, user_id,
            include_sources, exclude_sources,
        )

    async def list_user_tasks(
        self, user_id: str, limit: int = 50
    ) -> List[str]:
        """返回该 user 已有的 task 列表（最近修改的优先）。供 extractor prompt 引用。"""
        return await _run_in_pool(self._repository.list_user_tasks, user_id, limit)

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
            self._repository.append_trace,
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
            self._repository.recent_trace_payloads,
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
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> List["CodingMemory"]:
        """按 workspace_id（+可选 branch）纯过滤列出该 user 的完整 coding memory。

        作用域规则见 _scope_where（与 search 路径共用同一套）。无语义排序，按
        updated_at 倒序（最近修改优先）。include_sources / exclude_sources 按
        source 列在 SQL 里过滤（排除优先），所以 LIMIT/OFFSET 在过滤之后生效。
        """
        return await _run_in_pool(
            self._repository.list_by_workspace, user_id, workspace_id, branch, limit, offset,
            include_sources, exclude_sources,
        )


    async def stats(
        self,
        user_id: str,
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        include_sources: Optional[List[str]] = None,
        exclude_sources: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """经验数、最新创建/修改时间、active 事实数。纯聚合，不读正文。

        作用域与 list_by_workspace 相同：workspace_id 为空表示该 user 全部；
        来源过滤同样与 list_by_workspace 一致，count 因此恒等于分页可翻到的总数。
        """
        return await _run_in_pool(
            self._repository.stats, user_id, workspace_id, branch,
            include_sources, exclude_sources,
        )


    # ------------------------------------------------------------
    # 召回
    # ------------------------------------------------------------

    async def search_by_query_embedding(
        self,
        query_embedding: List[float],
        user_id: str,
        *,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        agent_ids: Optional[List[str]] = None,
        session_ids: Optional[List[str]] = None,
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
        agent_id / session_id（或 agent_ids / session_ids）是写授权作用域，
        传入时精确匹配；workspace_id / branch 的规则见 _scope_where。
        不传任何作用域则维持纯语义召回；传了则只保留业务库里落在作用域内的经验，
        并按选择率放大向量取数，避免"先取 top-N 再过滤"把召回吃掉。
        """
        if self._vec is None:
            return []

        effective_agent_ids = list(agent_ids) if agent_ids else (
            [agent_id] if agent_id else []
        )
        effective_session_ids = list(session_ids) if session_ids else (
            [session_id] if session_id else []
        )
        allowed: Optional[Set[str]] = None
        if effective_agent_ids or effective_session_ids or workspace_id:
            allowed, total = await _run_in_pool(
                self._repository.scoped_experience_ids,
                user_id,
                effective_agent_ids,
                effective_session_ids,
                workspace_id,
                branch,
            )
            if not allowed:
                logger.info(
                    f"[coding-store] no experience in scope: "
                    f"agent_id={agent_id!r} session_id={session_id!r} "
                    f"workspace_id={workspace_id!r} branch={branch!r} "
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
            # 认业务库那份而不是向量行 payload 里的 coding_workspace_id 副本：
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

    async def insert_fact(
        self, fact: WorkingFact, embedding: Optional[List[float]] = None,
    ) -> None:
        """新增一条事实（SQLite + 事实 collection 各一行）。

        embedding 可由调用方预先用 embed_fact() 算好（fact reconcile 要在覆盖旧
        事实之前确认新事实能向量化）；不传时这里算。向量化失败抛
        CodingWriteError(EMBEDDING_FAILED)，什么都不写。
        """
        if fact.created_at is None:
            fact.created_at = datetime.utcnow()
        # 先向量化再落业务库，理由同 insert()：不落没有向量的事实。
        if embedding is None:
            embedding = await self.embed_fact(fact)
        await _run_in_pool(self._repository.upsert_fact, fact)
        await self._upsert_fact_vec(fact, embedding=embedding)
        logger.info(f"[coding-store] inserted fact_id={fact.fact_id}")
        logger.debug(
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
        return await _run_in_pool(self._repository.get_facts, fact_ids, user_id)

    async def search_facts(
        self,
        query_embedding: List[float],
        user_id: str,
        *,
        agent_id: Optional[str] = None,
        session_id: Optional[str] = None,
        agent_ids: Optional[List[str]] = None,
        session_ids: Optional[List[str]] = None,
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

        agent_id / session_id（或 agent_ids / session_ids）是写授权作用域，
        传入时精确匹配；workspace_id / branch 的规则见 _scope_where。
        不传任何作用域则不过滤，传了按 SQLite 里的值筛，并按选择率放大向量取数。

        向量库按 top 的 4 倍过采样：向量行和 业务库行不是一个事务里写的，中间挂掉
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

        effective_agent_ids = list(agent_ids) if agent_ids else (
            [agent_id] if agent_id else []
        )
        effective_session_ids = list(session_ids) if session_ids else (
            [session_id] if session_id else []
        )
        fetch = top * 4
        if effective_agent_ids or effective_session_ids or workspace_id:
            scoped, total = await _run_in_pool(
                self._repository.count_active_facts,
                user_id,
                effective_agent_ids,
                effective_session_ids,
                workspace_id,
                branch,
            )
            if not scoped:
                logger.info(
                    f"[coding-store] no active fact in scope: "
                    f"agent_ids={effective_agent_ids!r} "
                    f"session_ids={effective_session_ids!r} "
                    f"workspace_id={workspace_id!r} branch={branch!r} "
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
            and (not effective_agent_ids or f.agent_id in effective_agent_ids)
            and (not effective_session_ids or f.session_id in effective_session_ids)
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
            self._repository.get_fact_ancestors, fact_id, user_id, max_depth
        )

    async def supersede_fact(self, old_fact_id: str, new_fact_id: str) -> bool:
        """旧事实软删：status=superseded + fact_link 指向新事实，并撤出向量库。

        业务库行保留，fact_link 链路可回溯；向量行删掉，不再占召回名额。
        """
        changed = await _run_in_pool(
            self._repository.supersede_fact, old_fact_id, new_fact_id
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
            self._repository.set_fact_derived_experiences, fact_id, experience_ids,
        )

    async def set_experience_source_facts(
        self, experience_id: str, fact_ids: List[str]
    ) -> None:
        """覆盖写 experience.source_fact_ids（调用方负责取并集）。"""
        await _run_in_pool(
            self._repository.set_experience_source_facts, experience_id, fact_ids,
        )

    # ------------------------------------------------------------
    # 时间与向量数据
    # ------------------------------------------------------------

    def _fill_timestamps(self, memory: CodingMemory) -> None:
        now = datetime.utcnow()
        if memory.created_at is None:
            memory.created_at = now
        memory.updated_at = now


    # ------------------------------------------------------------
    # VDB 实现
    # ------------------------------------------------------------

    @staticmethod
    def _vec_key_texts(memory: CodingMemory) -> List[Tuple[str, str]]:
        all_keys: List[Tuple[str, str]] = [("task", memory.task)]
        for k in memory.search_keys:
            if k:
                all_keys.append(("aspect", k))
        return all_keys

    async def _embed_vec_keys(
        self, memory: CodingMemory
    ) -> Optional[List[List[float]]]:
        """写入链路用：向量化 task + search_keys，失败抛 CodingWriteError。

        无 VDB 时返回 None（不需要向量）。
        """
        if self._vec is None:
            return None
        texts = [t for _, t in self._vec_key_texts(memory)]
        try:
            embeddings = await self.embed_service.embed_batch(texts)
        except Exception as e:
            logger.warning(f"[coding-store] embed_batch failed: {e}")
            raise CodingWriteError(
                "EMBEDDING_FAILED",
                f"coding experience embedding failed: {type(e).__name__}: {e}",
            ) from e
        if len(embeddings or []) != len(texts) or not all(embeddings):
            raise CodingWriteError(
                "EMBEDDING_FAILED",
                "coding experience embedding returned no vector",
            )
        return embeddings

    async def _upsert_vec_keys(
        self,
        memory: CodingMemory,
        embeddings: Optional[List[List[float]]] = None,
    ) -> None:
        """把 task + search_keys 写入 VDB（每行一个 MemoryNode）。

        embeddings 由写入链路预先算好（见 _embed_vec_keys）；不传时现算，
        失败只记 warning（edit_experience 的手动编辑路径沿用这一行为）。
        """
        if self._vec is None:
            return

        all_keys = self._vec_key_texts(memory)
        if not all_keys:
            return

        # batch embed
        texts = [t for _, t in all_keys]
        if embeddings is None:
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

    @staticmethod
    def _fact_vector_text(fact: WorkingFact) -> str:
        return f"{fact.subject}: {fact.statement}" if fact.subject else fact.statement

    async def embed_fact(self, fact: WorkingFact) -> Optional[List[float]]:
        """写入链路用：向量化事实检索文本，失败抛 CodingWriteError。

        无事实 VDB 或 statement 为空时返回 None（不需要向量）。
        """
        if self._fact_vec is None or not fact.statement:
            return None
        try:
            embedding = await self.embed_service.embed(self._fact_vector_text(fact))
        except Exception as e:
            logger.warning(f"[coding-store] fact embed failed: {e}")
            raise CodingWriteError(
                "EMBEDDING_FAILED",
                f"working fact embedding failed: {type(e).__name__}: {e}",
            ) from e
        if not embedding:
            raise CodingWriteError(
                "EMBEDDING_FAILED", "working fact embedding returned no vector",
            )
        return embedding

    async def _upsert_fact_vec(
        self, fact: WorkingFact, embedding: Optional[List[float]] = None,
    ) -> None:
        """事实入向量库。一条事实一行，node_id 就是 fact_id。

        检索文本是 statement，前面缀上 subject（有的话）。subject 通常已经在
        statement 里出现过，但点查类问题（"Dylan 第二天午餐吃什么"）成败全看主体
        这个词的权重，缀一遍把它从"顺带提到"提成"标题"。details 刻意不进向量：
        它是原文，长度不受控，embed 进去会把摘要的语义冲淡，而它要解决的问题是
        "命中之后读得到值"，不是"更容易命中"。
        """
        if self._fact_vec is None or not fact.statement:
            return
        vector_text = self._fact_vector_text(fact)
        if embedding is None:
            # edit_fact 的手动编辑路径：现算，失败只记 warning。
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
