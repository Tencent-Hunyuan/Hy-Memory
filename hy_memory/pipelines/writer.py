"""
HY Memory - Write (System 1)

写入流程:
  1. 参数校验
  2. 向量化 (EmbedService)
  3. Qdrant 持久化 (VectorStore) - 原始内容始终存为 L1_RAW
  4. [可选] MemAgent 智能处理 (提取实体、生成摘要、冲突检测)
     - Agent 提取的高层信息存为 L2_FACT/L3_SUMMARY（不再区分 L4_IDENTITY，统一 L2_FACT）
     - intentions（前瞻意图）存为 L7_INTENTION（带 valid_until，过期由 reader 惰性转 L2）
     - 提取成功后，L1_RAW 降级为 SHADOW 状态（不被召回）；
       提取结果为空（extractor 判定无可提取事实）时同样降级 ——
       「没什么可记」是正常结论，原始对话不应留在可召回层
  5. [可选] 合并检测 (Merger)

mode 行为差异:
  - lite:  只存 L1_RAW，不调 LLM，最简 embed 入库
  - pro:   L1_RAW + MemAgent 提取高层信息 → reconcile → L2/L4/L3
  - ultra: 同 pro，但 System2Writer 会在之后异步执行 System 2 认知加工
"""

import asyncio
import json
import os
import time
import weakref
from typing import Optional, Dict, Any, List, Tuple, Union
import uuid
from datetime import datetime
import logging

from .base import WritePipeline, WriteRequest, WriteResponse, PipelineContext
from ..config import MemoryConfig
from ..core.merger import Merger
from ..core.embed_service import EmbedService
from ..core.workload import (
    episode_slot,
    graph_slot,
    workload_snapshot,
    workload_scope,
)
from ..agent.mem_agent import MemAgent, ProcessMode
from ..agent.reconciler import MemoryReconciler
from ..agent.tools.basic_profile import upsert_basic_profile
from ..models.memory import MemoryNode, MemoryLayer, MemoryStatus, SourceType
from ..data.vector_store import create_vector_store
from ..data.vector_store_base import VectorStoreBase
from ..utils.tracer import PipelineTracer, create_tracer
from ..utils.log_setup import get_request_id
from ..utils.pipeline_observability import is_pipeline_trace_enabled
from ._retrieval import tag_index as _tag_index_helper
from ._retrieval.retrieval_clauses import (
    eligible_retrieval_clauses,
    sync_retrieval_clause_index,
)
from ._retrieval.session_episode import add_parent_episode_membership
from ..structure import merge_structural_custom, structural_custom

logger = logging.getLogger(__name__)

_RECONCILE_ENABLED = os.getenv("RECONCILE_ENABLED", "true").lower() == "true"
_MEMORY_AT_UNSET = object()


def _write_apply_verify_enabled() -> bool:
    """Enable expensive read-after-write audits only when explicitly requested."""
    return os.getenv("MEMORY_WRITE_APPLY_VERIFY_ENABLED", "false").lower() in (
        "true", "1", "yes",
    )

# 同一用户的候选判断、reconcile 和落库必须原子地串行。否则两个并发首写都可能
# 观察到空 L2/L4 并走 ADD 快速路径，制造重复。按 event loop 分桶，避免跨 loop 复用 Lock。
_RECONCILE_LOOP_USER_LOCKS = weakref.WeakKeyDictionary()


def _reconcile_user_lock(user_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _RECONCILE_LOOP_USER_LOCKS.get(loop)
    if locks is None:
        locks = weakref.WeakValueDictionary()
        _RECONCILE_LOOP_USER_LOCKS[loop] = locks
    lock = locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[user_id] = lock
    return lock


def _count_user_owned(metas: List[Dict]) -> int:
    """数 user 侧记忆条数（owner 缺省视为 user，与落库时的默认一致）。"""
    return sum(
        1 for m in (metas or [])
        if (m.get("owner") or "user") == "user"
    )


def _norm_owner(value: Any) -> Optional[str]:
    """归一化 extractor/reconcile 给出的 owner：仅接受 'user' / 'agent'，否则 None。"""
    if not value:
        return None
    v = str(value).strip().lower()
    if v in ("user", "agent"):
        return v
    # 兼容 mem0 风格的 'assistant' → 'agent'
    if v == "assistant":
        return "agent"
    return None


class MemoryWriter(WritePipeline):
    """
    核心写入器 (System 1)

    写入流程: 原始内容存 L1_RAW + 单路 Qdrant 存储 + 可选 MemAgent LLM 提取高层信息
    lite 模式不调 LLM，pro/ultra 模式调 MemAgent。
    """

    VERSION = "writer"

    def __init__(
        self,
        config: MemoryConfig,
        embed_service: Optional[EmbedService] = None,
        vector_store: Optional[VectorStoreBase] = None,
        cache=None,
        graph_store=None,
    ):
        self.config = config
        self._embed_service = embed_service
        self._external_vector_store = vector_store
        self._cache = cache
        # 记忆关系图（pro 模式）。None 表示图未启用 → 投影整段跳过。
        self._graph_store = graph_store

        self._merger: Optional[Merger] = None
        self._mem_agent: Optional[MemAgent] = None
        self._reconciler: Optional[MemoryReconciler] = None
        self._vector_store: Optional[VectorStoreBase] = None
        self._vector_store_initialized = False
        self._existing_tags_cache: Dict[Tuple[str, str], Tuple[float, Tuple[str, ...]]] = {}
        self._existing_tags_locks: Dict[Tuple[str, str], asyncio.Lock] = {}

        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        if self._embed_service is None:
            self._embed_service = EmbedService(self.config)
        self._merger = Merger()
        self._mem_agent = MemAgent(self.config)
        self._reconciler = MemoryReconciler(
            self.config,
            llm_provider=self._mem_agent.llm_provider,
        )
        self._vector_store = self._external_vector_store or create_vector_store(self.config)
        if self._external_vector_store and getattr(self._external_vector_store, '_client', None):
            self._vector_store_initialized = True
        self._initialized = True
        logger.debug("MemoryWriter initialized")

    @property
    def embed_service(self) -> EmbedService:
        if self._embed_service is None:
            self._embed_service = EmbedService(self.config)
        return self._embed_service

    @property
    def merger(self) -> Merger:
        if self._merger is None:
            self._merger = Merger()
        return self._merger

    @property
    def mem_agent(self) -> MemAgent:
        if self._mem_agent is None:
            self._mem_agent = MemAgent(self.config)
        return self._mem_agent

    @property
    def llm_provider(self):
        """Shared chat LLM used by extraction and explicit episode finalization."""
        return self.mem_agent.llm_provider

    @property
    def reconciler(self) -> MemoryReconciler:
        if self._reconciler is None:
            self._reconciler = MemoryReconciler(
                self.config,
                llm_provider=self.mem_agent.llm_provider,
            )
        return self._reconciler

    async def _get_vector_store(self) -> VectorStoreBase:
        if self._vector_store is None:
            self._vector_store = self._external_vector_store or create_vector_store(self.config)
        if not self._vector_store_initialized:
            await self._vector_store.initialize()
            self._vector_store_initialized = True
        return self._vector_store

    # ================================================================
    # 私有辅助方法
    # ================================================================

    @staticmethod
    def _build_custom(op, request_id: str, request: Optional[WriteRequest] = None) -> Dict[str, Any]:
        """构造 VDB payload 中的 custom 字段。始终包含 request_id 以便追溯。"""
        custom: Dict[str, Any] = {}
        if request_id:
            custom["request_id"] = request_id
        if op is not None and op.supersede_reason:
            custom["supersede_reason"] = op.supersede_reason
        MemoryWriter._attach_client_meta(custom, request)
        return custom

    def _session_episode_id(self, request: WriteRequest) -> str:
        """Stable parent id for facts when first-class session episodes are on."""
        if not getattr(self.config.recall, "session_episode_enabled", False):
            return ""
        from ..agent.episode_summary import session_episode_id
        return session_episode_id(
            request.user_id, request.agent_id or "default_agent",
            request.session_id or "default_session",
        )

    def _fact_custom(self, op, request_id: str, request: WriteRequest) -> Dict[str, Any]:
        custom = self._build_custom(op, request_id, request)
        if op is not None and getattr(
            getattr(self.config, "llm", None), "structure_metadata_enabled", False
        ):
            custom = merge_structural_custom(
                custom,
                topic_paths=getattr(op, "topic_paths", None),
                event_facet=getattr(op, "event_facet", None),
                topic_assignments=getattr(op, "topic_assignments", None),
                topic_paths_provided=bool(
                    getattr(op, "topic_paths_provided", False)
                    or getattr(op, "topic_paths", None)
                ),
                event_facet_provided=bool(
                    getattr(op, "event_facet_provided", False)
                    or getattr(op, "event_facet", None)
                ),
                topic_assignments_provided=bool(
                    getattr(op, "topic_assignments_provided", False)
                    or getattr(op, "topic_assignments", None)
                ),
            )
        parent = self._session_episode_id(request)
        if parent:
            custom = add_parent_episode_membership(
                custom, parent, similarity=1.0, method="write_session",
            )
        return custom

    @staticmethod
    def _merge_source_raw_ids(
        old_node: Optional[MemoryNode],
        source_raw_memory_id: str,
    ) -> Dict[str, Any]:
        """Preserve every raw source represented by an in-place fact merge."""
        custom = dict(getattr(old_node, "custom", None) or {})
        source_ids = list(custom.get("source_raw_memory_ids") or [])
        legacy_source = str(getattr(old_node, "source_raw_memory_id", "") or "")
        if legacy_source and legacy_source not in source_ids:
            source_ids.append(legacy_source)
        if (
            "s2_reviewed_source_count" not in custom
            and (int(custom.get("s2_reviewed_count") or 0) > 0
                 or int(custom.get("s2_evidence_count") or 0) > 0)
        ):
            # Freeze the provenance consumed by the previous evoling pass
            # before appending this write. The new source then reopens the node.
            custom["s2_reviewed_source_count"] = len(source_ids)
        for source_id in (str(source_raw_memory_id or ""),):
            if source_id and source_id not in source_ids:
                source_ids.append(source_id)
        if source_ids:
            custom["source_raw_memory_ids"] = source_ids
        return custom

    @staticmethod
    def _attach_client_meta(custom: Dict[str, Any], request: Optional[WriteRequest]) -> None:
        """给新建节点挂上调用方 meta（单份 list）。

        meta 存成 list 而非 dict：每份 meta 代表「该次 write 时刻的上下文」，
        同一节点被后续写入合并时追加而非覆盖（见 _merge_client_meta）。
        新建节点自然只有一份。
        """
        meta = getattr(request, "client_meta", None) if request is not None else None
        if meta:
            custom["meta"] = [dict(meta)]

    @staticmethod
    def _merge_client_meta(
        old_custom: Optional[Dict[str, Any]],
        request: Optional[WriteRequest],
    ) -> Optional[Dict[str, Any]]:
        """把本次 meta 追加到旧节点已有的 meta list 上，返回待写回的完整 custom。

        用于原地更新（UPDATE）——节点身份不变，旧 meta 必须保留，否则该记忆
        此前几次写入的上下文就丢了。

        返回 None 表示无需改动 custom（本次没传 meta），调用方据此跳过回读与写回，
        使未使用该功能的调用方零额外开销。
        """
        meta = getattr(request, "client_meta", None) if request is not None else None
        if not meta:
            return None

        merged = dict(old_custom or {})
        existing = merged.get("meta")
        # 容错：历史数据或人工改写可能让 meta 不是 list，此时不覆盖已有内容，
        # 把它降级成 list 的首元素，避免静默丢数据
        if isinstance(existing, list):
            chain = list(existing)
        elif existing:
            chain = [existing]
        else:
            chain = []
        chain.append(dict(meta))
        merged["meta"] = chain
        return merged

    async def _maybe_index_entities(
        self, vector_store, node, request,
    ) -> Any:
        """若 entity_store 开关开启且为 L2_FACT，落库后刷 entity store（best-effort）。

        覆盖 reconcile（ADD/UPDATE/SUPERSEDE）与首写（_direct_store）两条路。
        """
        try:
            if not getattr(self.config.recall, "entity_store_enabled", False):
                return
            if node is None or node.layer != MemoryLayer.L2_FACT:
                return
            from ._retrieval.entity_store import index_memory_entities
            await index_memory_entities(
                vector_store=vector_store,
                embed_service=self.embed_service,
                memory_id=node.node_id,
                content=node.content or "",
                user_id=request.user_id,
                agent_id=request.agent_id or "default_agent",
            )
        except Exception as e:
            logger.debug(f"[entity-index] skipped (non-fatal): {e}")

    @staticmethod
    def _collect_new_memories(
        extracted_info: Dict[str, Any],
    ) -> Tuple[List[str], List[Dict]]:
        """
        从 extract 结果中收集新 memory 文本列表和完整 meta 列表。

        注意：basic_info 字段由 extractor 在 JSON 输出中返回，writer 单独处理
        （走 upsert_basic_profile() 落 L0_BASIC_INFO 演化链），这里不再收集。

        Returns:
            (new_memory_texts, new_memories_meta)
            每条 meta: {"content", "layer", "tags"}
        """
        new_memory_texts: List[str] = []
        new_memories_meta: List[Dict] = []

        # 1) memory → 每条独立 memory（统一 L2_FACT）
        #    新版 extractor 输出 `memory`；兼容旧版 `facts` 字段。
        for item in (extracted_info.get("memory") or extracted_info.get("facts") or []):
            if not isinstance(item, dict):
                continue
            content = item.get("content", "")
            if not content:
                continue
            new_memory_texts.append(content)
            new_memories_meta.append({
                "content": content,
                "layer": "L2_FACT",
                "tags": item.get("tags") or [],
                "owner": _norm_owner(item.get("owner")),
                "topic_paths": item.get("topic_paths") or [],
                "event_facet": item.get("event_facet") or {},
                "topic_assignments": item.get("topic_assignments") or [],
                "topic_paths_provided": "topic_paths" in item,
                "event_facet_provided": "event_facet" in item,
                "topic_assignments_provided": "topic_assignments" in item,
            })

        # 2) 向后兼容：旧 extractor 输出的 identity 也并入 L2_FACT
        #    （新版 extractor 不再产出 identity；L4_IDENTITY 不再写入，仅读历史数据）
        for item in (extracted_info.get("identity") or []):
            if not isinstance(item, dict):
                continue
            content = item.get("content", "")
            if not content:
                continue
            new_memory_texts.append(content)
            new_memories_meta.append({
                "content": content,
                "layer": "L2_FACT",
                "tags": item.get("tags") or [],
                "owner": _norm_owner(item.get("owner")),
                "topic_paths": item.get("topic_paths") or [],
                "event_facet": item.get("event_facet") or {},
                "topic_assignments": item.get("topic_assignments") or [],
                "topic_paths_provided": "topic_paths" in item,
                "event_facet_provided": "event_facet" in item,
                "topic_assignments_provided": "topic_assignments" in item,
            })

        # 3) 兼容旧版 extract 输出（profile + facts）— 不处理 basic_info
        if not new_memory_texts:
            profile = extracted_info.get("profile", {})
            if profile and isinstance(profile, dict):
                profile_items = []
                for k, v in profile.items():
                    if k == "preferences":
                        continue
                    if v and str(v).lower() not in ("null", "none", ""):
                        profile_items.append(f"{k}: {v}")
                prefs = profile.get("preferences", [])
                if prefs and isinstance(prefs, list):
                    pref_strs = [str(p) for p in prefs if p]
                    if pref_strs:
                        profile_items.append(f"preferences: {', '.join(pref_strs)}")
                if profile_items:
                    t = "; ".join(profile_items)
                    new_memory_texts.append(t)
                    new_memories_meta.append({"content": t, "layer": "L2_FACT", "tags": []})
            for f in (extracted_info.get("facts") or []):
                if isinstance(f, dict) and f.get("content"):
                    new_memory_texts.append(f["content"])
                    new_memories_meta.append({"content": f["content"], "layer": "L2_FACT", "tags": f.get("tags") or []})

        return new_memory_texts, new_memories_meta

    async def _dedup_extracted(
        self,
        new_memory_texts: List[str],
        new_memories_meta: List[Dict],
        request: "WriteRequest",
        req_id: str,
    ) -> Tuple[List[str], List[Dict], Dict[str, List[float]]]:
        """对 extractor 新抽取的多条 memory 互相去重（入库前）。

        这些条目还没落库、无 node_id、无演化链 → 全按非链处理，额外 embed 一次，
        delete_from_store=False（只丢弃重复项，不删库），并记 DEDUP log。
        保留：用列表下标做确定性 gmt（越靠前越优先保留，等价 extractor 输出顺序）。

        embed：优先一次 batch HTTP；批请求失败时才降级为逐条并行，兼顾正常路径吞吐
        与故障时的部分成功。DEDUP log.parsed 记录墙钟与每条 meta。
        """
        import asyncio
        import time
        from ..pipelines._retrieval.dedup import DedupItem, execute_dedup

        async def _embed_one(text: str):
            t0 = time.perf_counter()
            try:
                vec, meta = await self.embed_service.embed_queued_with_meta(text)
                meta = dict(meta or {})
                meta["elapsed_ms"] = round((time.perf_counter() - t0) * 1000, 2)
                return vec, meta
            except Exception as e:
                return e

        # 所有文本并发进入 write queue，由 EmbedService 按 queue_batch_size 攒批。
        # PMV2 单 chunk 抽出 15 条时，默认从 15 个 HTTP 收敛到约 2 个批请求；相比
        # 强制塞进一个超大 batch，它保留较低的单请求墙钟并允许跨 write 合并。
        t_wall0 = time.perf_counter()
        embed_results = await asyncio.gather(
            *[_embed_one(t) for t in new_memory_texts],
            return_exceptions=True,
        )
        embed_elapsed_ms = round((time.perf_counter() - t_wall0) * 1000, 2)
        per_item_elapsed_ms = round(
            embed_elapsed_ms / max(1, len(new_memory_texts)), 2,
        )

        embeds: List[Any] = []
        embed_metas: List[Dict[str, Any]] = []
        for i, r in enumerate(embed_results):
            if isinstance(r, Exception):
                logger.warning(
                    f"[write] extractor dedup embed[{i}] failed: {r}"
                )
                embeds.append(None)
                embed_metas.append({
                    "index": i,
                    "error": type(r).__name__,
                    "error_msg": str(r)[:300],
                })
            elif isinstance(r, tuple) and len(r) == 2:
                vec, meta = r
                embeds.append(vec)
                m = dict(meta or {})
                m["index"] = i
                m.setdefault("elapsed_ms", per_item_elapsed_ms)
                embed_metas.append(m)
            else:
                embeds.append(None)
                embed_metas.append({
                    "index": i,
                    "error": "unexpected_result",
                    "error_msg": repr(r)[:300],
                })

        items: List[DedupItem] = []
        for i, (text, emb) in enumerate(zip(new_memory_texts, embeds)):
            if not emb:
                continue
            items.append(DedupItem(
                node_id=str(i),               # 用下标作临时 id
                embedding=emb,
                content=text,
                is_latest=True,
                is_chain_head=False,
                gmt_created=float(i),         # 越靠前 gmt 越小 → 优先保留
                chain_node_ids=[str(i)],
            ))

        embed_info = {
            "embedding_source": "embed_service",
            "embed_elapsed_ms": embed_elapsed_ms,
            "embed_count": len(new_memory_texts),
            "embed_ok_count": sum(1 for e in embeds if e),
            "embed_metas": embed_metas,
        }

        # 即使 <2 条可比较，也落 DEDUP log，便于审计 embed 耗时 / provider meta
        plan = await execute_dedup(
            items,
            vector_store=None,                # 还没入库
            cache=self._cache,
            trigger="extractor",
            request_id=req_id,
            user_id=request.user_id,
            agent_id=request.agent_id or "default_agent",
            delete_from_store=False,
            extra_parsed=embed_info,
        )
        drop_idx = {int(d) for d in plan.get("delete_ids", [])}
        kept_embeddings = {
            text: emb
            for text, emb in zip(new_memory_texts, embeds)
            if text and emb
        }
        if not drop_idx:
            return new_memory_texts, new_memories_meta, kept_embeddings

        kept_texts, kept_meta = [], []
        for i, (t, m) in enumerate(zip(new_memory_texts, new_memories_meta)):
            if i in drop_idx:
                continue
            kept_texts.append(t)
            kept_meta.append(m)
        logger.info(
            f"[write] extractor dedup dropped {len(drop_idx)} of {len(new_memory_texts)} extracted"
        )
        kept_embedding_map = {
            text: kept_embeddings[text]
            for text in kept_texts
            if text in kept_embeddings
        }
        return kept_texts, kept_meta, kept_embedding_map

    @staticmethod
    def _collect_intentions(extracted_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        从 extract 结果中收集 intentions（前瞻意图，存 L7_INTENTION）。

        每条 intention: {"content", "tags", "valid_until"}
        valid_until 是 extractor 输出的 ISO 日期串（或 null）；这里解析为
        datetime（当天 23:59:59，宽松到日末），解析失败/缺失 → None。
        """
        out: List[Dict[str, Any]] = []
        for item in (extracted_info.get("intentions") or []):
            if not isinstance(item, dict):
                continue
            content = (item.get("content") or "").strip()
            if not content:
                continue
            valid_until: Optional[datetime] = None
            raw_vu = item.get("valid_until")
            if raw_vu and str(raw_vu).lower() not in ("null", "none", ""):
                try:
                    # 支持 "YYYY-MM-DD" 或完整 ISO；统一拉到当天日末
                    d = datetime.fromisoformat(str(raw_vu).strip())
                    valid_until = d.replace(hour=23, minute=59, second=59, microsecond=0)
                except (ValueError, TypeError):
                    valid_until = None
            out.append({
                "content": content,
                "tags": item.get("tags") or [],
                "valid_until": valid_until,
                "owner": _norm_owner(item.get("owner")),
            })
        return out

    async def _store_intentions(
        self,
        intentions: List[Dict[str, Any]],
        request: WriteRequest,
        vector_store: VectorStoreBase,
        req_id: str,
    ) -> List[str]:
        """
        把 intentions 直接 upsert 为 L7_INTENTION 节点（不走 reconcile）。

        意图是 point-in-time 信号，不与已有 fact 合并；过期后由 reader 惰性
        转成 L2_FACT。返回新建节点 id 列表。

        **批内 dedup（0805 修复）**：extractor 一次可能给出多条 cosine≥0.95
        的近似意图（LLM 复读），本函数在 embed 之后 upsert 之前做批内互斥判重：
            规则：同一天 valid_until（都为 None 或 date 相等）+ cosine ≥
                  MEMORY_DEDUP_THRESHOLD（默认 0.95）→ 视为复本
            （memory_at 同 request 天然相同，无需比对）
            保留：列表内位置最靠前的一条（即 extractor 输出顺序里最先的）
        不查 VDB、不做"新写入 vs 库里已有"的对比，避免给 VDB 增加读压力。
        库内存量污染由 recall 侧异步删除逐步清理。best-effort：任何异常都
        回退到"不判重、全写入"，绝不影响 upsert 主流程。
        """
        if not intentions:
            return []

        # 批量 embed
        contents = [it["content"] for it in intentions]
        embeddings: List[Optional[List[float]]] = [None] * len(contents)
        try:
            batch = await self.embed_service.embed_batch(contents)
            for i, emb in enumerate(batch):
                embeddings[i] = emb
        except Exception as e:
            logger.warning(f"[intention] batch embed failed, falling back to sequential: {e}")

        # 单条 embed 补漏（embed_batch 失败时按序补 embed）
        for i, it in enumerate(intentions):
            if embeddings[i] is None:
                try:
                    embeddings[i] = await self.embed_service.embed_queued(it["content"])
                except Exception as e:
                    logger.error(f"[intention] embed failed, skip: {e}")

        # ============ 批内 dedup（不查 VDB，只对本批 intentions 互斥判重） ============
        keep_mask: List[bool] = [True] * len(intentions)
        try:
            dropped = self._intention_batch_dedup(intentions, embeddings)
            for i in dropped:
                if 0 <= i < len(keep_mask):
                    keep_mask[i] = False
            if dropped:
                logger.info(
                    f"[intention] batch dedup dropped {len(dropped)} of "
                    f"{len(intentions)} extracted intentions"
                )
        except Exception as e:
            # 判重失败 → 回退到"全部 upsert"（保守）
            logger.warning(f"[intention] batch dedup failed (skip, upsert all): {e}")

        stored: List[str] = []
        for i, it in enumerate(intentions):
            if not keep_mask[i]:
                continue
            emb = embeddings[i]
            if emb is None:
                continue
            node = MemoryNode(
                user_id=request.user_id,
                agent_id=request.agent_id or "default_agent",
                session_id=request.session_id or "default_session",
                source=request.source,
                layer=MemoryLayer.L7_INTENTION,
                content=it["content"],
                owner=it.get("owner") or "user",
                supersedes=None,
                is_latest=True,
                source_type=SourceType.INFERRED,
                status=MemoryStatus.ACTIVE,
                embedding=emb,
                memory_at=request.memory_at,
                valid_until=it.get("valid_until"),
                tags=list(it.get("tags") or []),
                custom=self._build_custom(None, req_id, request),
            )
            try:
                nid = await vector_store.upsert(node)
                stored.append(nid)
                logger.debug(
                    f"[intention] L7 upsert: {it['content'][:80]} → node_id={nid} "
                    f"valid_until={it.get('valid_until')}"
                )
                # INTENTION_UPSERT pipeline log（意图不走 reconcile，单独打点）+
                # memory_operations 审计。带 memory_id 供线上按 request_id / memory_id 追查。
                await self._emit_intention_upsert(
                    request,
                    vector_store=vector_store,
                    memory_id=nid,
                    content=it["content"],
                    tags=list(it.get("tags") or []),
                    valid_until=it.get("valid_until"),
                )
                if self._cache:
                    try:
                        await self._cache.store_memory_operation(
                            request_id=req_id,
                            user_id=request.user_id,
                            agent_id=request.agent_id or "default_agent",
                            op="INTENTION_UPSERT",
                            memory_id=nid,
                            content=it["content"],
                            layer=MemoryLayer.L7_INTENTION.value,
                            reason="intention (S1 direct upsert, no reconcile)",
                            supersedes=[],
                        )
                    except Exception as e:
                        logger.warning(f"[intention] store_memory_operation failed: {e}")
            except Exception as e:
                logger.error(f"[intention] upsert failed: {e}")
        return stored

    @staticmethod
    def _intention_batch_dedup(
        intentions: List[Dict[str, Any]],
        embeddings: List[Optional[List[float]]],
    ) -> List[int]:
        """批内 L7 意图去重（纯函数）。

        判重规则：
          同一天 valid_until（都 None 或 date 相等）+ cosine≥MEMORY_DEDUP_THRESHOLD
          → 视为复本。同组保留 index 最小者，其余进 drop_idx。

        memory_at 因为同属一次 write request，天然相同，此处不再比对。
        本函数不 I/O、无 side-effect，测试友好。

        Returns:
            List[int]：需要丢弃的 intentions index 列表（升序）。
        """
        import os

        try:
            threshold = float(os.environ.get("MEMORY_DEDUP_THRESHOLD", 0.95))
        except (TypeError, ValueError):
            threshold = 0.95

        def _vu_key(it: Dict[str, Any]) -> Any:
            """valid_until 归一化到 date 键：None → None；datetime → date。"""
            vu = it.get("valid_until")
            if vu is None:
                return None
            try:
                return vu.date() if hasattr(vu, "date") else vu
            except Exception:
                return None

        def _cosine(a: List[float], b: List[float]) -> float:
            if not a or not b:
                return float("-inf")
            la = sum(x * x for x in a) ** 0.5
            lb = sum(x * x for x in b) ** 0.5
            if la == 0.0 or lb == 0.0:
                return float("-inf")
            return sum(x * y for x, y in zip(a, b)) / (la * lb)

        n = len(intentions)
        if n < 2:
            return []

        # 按 valid_until 分桶
        buckets: Dict[Any, List[int]] = {}
        for i in range(n):
            if embeddings[i] is None:
                continue
            buckets.setdefault(_vu_key(intentions[i]), []).append(i)

        drop_idx = set()
        for key, idx_list in buckets.items():
            if len(idx_list) < 2:
                continue
            # 桶内两两比 cosine，>=threshold → 后者被丢弃（保留 index 最小）
            for a_pos in range(len(idx_list)):
                ia = idx_list[a_pos]
                if ia in drop_idx:
                    continue
                for b_pos in range(a_pos + 1, len(idx_list)):
                    ib = idx_list[b_pos]
                    if ib in drop_idx:
                        continue
                    if _cosine(embeddings[ia], embeddings[ib]) >= threshold:
                        drop_idx.add(ib)

        return sorted(drop_idx)

    # ================================================================
    # Episode（内部会话）：把跨 write 批次的"一件事"归到同一个 episode_id
    # ================================================================
    #
    # 为什么需要它：一次讨论往往跨越好几次 `write()`（十几二十轮），从中抽出的 memory
    # 属于同一件事。给它们打同一个 episode_id，读侧就能用它**框出一片候选**做加权。
    #
    # 为什么不建时间边：一天 20 条记忆两两相连是 190 条边、度数 19，一跳把当天全拉进来。
    # 一个字段 + 一次过滤查询就能达到同样效果，而且**不依赖图**（graph 关掉也能用）。
    #
    # 为什么不复用 session_id：那是调用方给的**业务隔离键**（进 isolation_key），
    # 可能很长（整段会话）或干脆是 default_session，粒度对不上"一次讨论"。
    #
    # 划分规则不用 LLM：距上次写入的间隔在 `episode_gap_seconds` 内算同一 episode，
    # 超了开新的；条数超过 `episode_max_memories` 也强制滚动，避免长会话变成一个巨型 episode。

    async def _resolve_episode_id(self, request: WriteRequest, n_new: int) -> str:
        """取本次写入应归属的 episode_id；cache 不可用时返回空串（功能自然降级）。"""
        if self._cache is None:
            return ""
        rcfg = getattr(self.config, "recall", None)
        gap = float(getattr(rcfg, "episode_gap_seconds", 1800.0) or 1800.0)
        cap = int(getattr(rcfg, "episode_max_memories", 50) or 50)

        key = "::".join([
            request.user_id or "default",
            request.agent_id or "default_agent",
            request.session_id or "default_session",
        ])
        now_ts = (
            request.memory_at.timestamp() if request.memory_at
            else datetime.now().timestamp()
        )
        try:
            state = await self._cache.get_episode_state(key)
            if state and state.get("episode_id"):
                idle = now_ts - float(state.get("last_write_ts") or 0)
                count = int(state.get("memory_count") or 0)
                if 0 <= idle <= gap and count < cap:
                    episode_id = state["episode_id"]
                    count += n_new
                else:
                    # 上一个 episode 就此关闭 —— 它不会再有新内容进来，所以这是总结它的时机。
                    # 只总结一次、无需把旧摘要喂回 LLM 做 merge（对比 rolling summary 的
                    # 每 N 轮重写一遍）。
                    self._schedule_episode_summary(request, state["episode_id"])
                    episode_id, count = f"ep_{uuid.uuid4().hex[:16]}", n_new
            else:
                episode_id, count = f"ep_{uuid.uuid4().hex[:16]}", n_new

            await self._cache.upsert_episode_state(
                key,
                user_id=request.user_id or "",
                agent_id=request.agent_id or "default_agent",
                session_id=request.session_id or "default_session",
                episode_id=episode_id,
                last_write_ts=now_ts,
                memory_count=count,
            )
            await self._log_episode(
                request,
                episode_id=episode_id,
                reused=(state or {}).get("episode_id") == episode_id,
                idle_s=(
                    now_ts - float((state or {}).get("last_write_ts") or 0)
                    if state else None
                ),
                count=count,
                n_new=n_new,
                gap=gap,
                cap=cap,
            )
            return episode_id
        except Exception as e:
            logger.debug(f"[episode] resolve failed (non-fatal): {e}")
            return ""

    def _make_trace_sink(self, request: WriteRequest, step: str):
        """给后台 summary 任务一个 `trace(prompt, response, parsed, elapsed_ms)` 回调。

        这些任务在 `agent/` 和 `_graph/` 里，本身不持有 cache/request 上下文；
        传一个闭包比让它们各自拼一套埋点参数更不容易漏。
        """
        if not self._cache or not is_pipeline_trace_enabled():
            return None
        req_id = request.request_id or get_request_id() or ""
        user_id = request.user_id
        agent_id = request.agent_id or "default_agent"
        session_id = request.session_id or ""

        async def _sink(
            *, prompt: str = "", response: str = "",
            parsed: Optional[Dict[str, Any]] = None,
            memory_ids: Optional[List[str]] = None,
            elapsed_ms: float = 0.0,
            tokens: Optional[Dict[str, int]] = None,
        ) -> None:
            try:
                tk = tokens or {}
                await self._cache.store_pipeline_log(
                    request_id=req_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    session_id=session_id,
                    step=step,
                    prompt=prompt,
                    response=response,
                    parsed=json.dumps(parsed or {}, ensure_ascii=False, default=str),
                    memory_ids=memory_ids or None,
                    elapsed_ms=float(elapsed_ms or 0.0),
                    prompt_tokens=int(tk.get("prompt", 0) or 0),
                    completion_tokens=int(tk.get("completion", 0) or 0),
                    total_tokens=int(tk.get("total", 0) or 0),
                )
            except Exception as e:
                logger.debug(f"[trace] {step} failed: {e}")

        return _sink

    async def _log_episode(
        self,
        request: WriteRequest,
        *,
        episode_id: str,
        reused: bool,
        idle_s: Optional[float],
        count: int,
        n_new: int,
        gap: float,
        cap: int,
    ) -> None:
        """episode 划分决策埋点。

        划分是后面一切的前提（读侧框选、摘要触发都跟着它走），所以**决策依据要能复盘**：
        沿用还是新开、距上次多久、累计几条、阈值是多少。少了它线上只能看到"摘要没触发"
        或"框出的候选不对"，无法归因到切分本身。
        """
        if not self._cache or not is_pipeline_trace_enabled():
            return
        parsed = {
            "episode_id": episode_id,
            "decision": "reuse" if reused else "new",
            "idle_seconds": round(idle_s, 3) if idle_s is not None else None,
            "user_memories_total": count,
            "user_memories_new": n_new,
            "gap_seconds": gap,
            "max_memories": cap,
        }
        try:
            await self._cache.store_pipeline_log(
                request_id=request.request_id or get_request_id() or "",
                user_id=request.user_id,
                agent_id=request.agent_id or "default_agent",
                session_id=request.session_id or "",
                step="EPISODE",
                prompt="",
                response=json.dumps(parsed, ensure_ascii=False, default=str),
                parsed=json.dumps(parsed, ensure_ascii=False, default=str),
                elapsed_ms=0,
            )
        except Exception as e:
            logger.debug(f"[episode] EPISODE trace failed: {e}")

    def _schedule_episode_summary(self, request: WriteRequest, episode_id: str) -> None:
        """episode 关闭时后台总结它一次（fire-and-forget，不阻塞写入）。"""
        rcfg = getattr(self.config, "recall", None)
        if not episode_id or rcfg is None:
            return
        if not getattr(rcfg, "episode_summary_enabled", False):
            return
        if self.llm_provider is None or self.embed_service is None:
            return
        try:
            from ..agent.episode_summary import summarize_closed_episode

            async def _run():
                try:
                    nid = await summarize_closed_episode(
                        vector_store=await self._get_vector_store(),
                        embed_service=self.embed_service,
                        llm_provider=self.llm_provider,
                        episode_id=episode_id,
                        user_id=request.user_id,
                        agent_id=request.agent_id,
                        session_id=request.session_id or "",
                        min_memories=int(
                            getattr(rcfg, "episode_summary_min_memories", 3) or 3
                        ),
                        # 这是一次 LLM 调用：prompt / 产出必须能在线上追到，
                        # 否则 badcase 只能看到"摘要内容不对"却拿不到输入
                        trace=self._make_trace_sink(request, "EPISODE_SUMMARY"),
                    )
                    if nid:
                        logger.info(
                            f"[episode-summary] episode={episode_id[:12]} → {nid[:12]}"
                        )
                except Exception as e:
                    logger.warning(f"[episode-summary] failed (non-fatal): {e}")

            asyncio.create_task(_run())
        except Exception as e:
            logger.debug(f"[episode-summary] schedule failed: {e}")

    # ================================================================
    # 记忆关系图（pro 模式，docs/pro_graph_design.md）
    # ================================================================

    @staticmethod
    def _graph_stub_node(
        node_id: str, content: str, layer, tags: List[str], request: WriteRequest,
        *, memory_at: Any = _MEMORY_AT_UNSET,
        custom: Optional[Dict[str, Any]] = None,
    ) -> MemoryNode:
        """给 UPDATE（原地改）造一个轻量 MemoryNode，供图投影使用。

        UPDATE 走的是 `update_payload`，手上没有 MemoryNode 对象；而图侧只需要
        node_id / content / tags / layer / isolation_key，不需要 embedding。
        """
        return MemoryNode(
            node_id=node_id,
            user_id=request.user_id,
            agent_id=request.agent_id or "default_agent",
            session_id=request.session_id or "default_session",
            source=request.source,
            layer=layer,
            content=content,
            tags=list(tags or []),
            custom=dict(custom or {}),
            memory_at=(request.memory_at if memory_at is _MEMORY_AT_UNSET else memory_at),
        )

    def _schedule_graph_projection(
        self,
        *,
        graph_payload: List[Tuple[Any, Any]],
        superseded_old_ids: List[str],
        candidate_scores: Dict[str, List[Tuple[str, float]]],
        request: Optional[WriteRequest] = None,
    ) -> None:
        """尽早启动本次图投影；同步 write 返回前等待当前请求完成。

        Graph 与 Episode 可以重叠，但投影数受进程级 gate 限制。Kuzu 的
        executor 只有一个 worker，因此 Kuzu 投影按 request 级串行，避免多个
        projection 的细粒度 Cypher 调用交错成超长队列。
        """
        graph_store = getattr(self, "_graph_store", None)
        if graph_store is None or not graph_payload:
            return None
        gcfg = getattr(self.config, "graph_store", None)
        if gcfg is not None and not getattr(gcfg, "memory_graph_write", True):
            return None

        async def _run_unbounded() -> None:
            try:
                from ._graph.projector import (
                    project_memories_to_graph, sync_graph_on_supersede,
                )

                nodes = [n for n, _op in graph_payload]
                # op.related 里的 target_id 是 existing memory 的 id
                related_by_node = {
                    n.node_id: list(getattr(op, "related", None) or [])
                    for n, op in graph_payload
                    if getattr(op, "related", None)
                }
                # 候选池分数按 content 对齐到刚落库的 node_id
                neighbors_by_node = {}
                for n, _op in graph_payload:
                    cands = (candidate_scores or {}).get(n.content or "")
                    if cands:
                        neighbors_by_node[n.node_id] = cands

                _t_proj = datetime.now()
                _proj_stats = await project_memories_to_graph(
                    graph_store=graph_store,
                    embed_service=self.embed_service,
                    nodes=nodes,
                    vector_store=await self._get_vector_store(),
                    related_by_node=related_by_node,
                    neighbors_by_node=neighbors_by_node,
                    edge_min_score=getattr(gcfg, "edge_min_score", 0.55),
                    co_occurs_max_batch=getattr(gcfg, "co_occurs_max_batch", 8),
                    temporal_spine=getattr(gcfg, "temporal_spine", False),
                    hierarchy_topic_write=getattr(
                        gcfg, "hierarchy_topic_write", False,
                    ),
                )
                if superseded_old_ids:
                    await sync_graph_on_supersede(graph_store, superseded_old_ids)
                # 图是异步 fire-and-forget 建的，失败只告警 —— 没有埋点就完全看不见
                # 它到底建没建、建了多少（读侧"图没起作用"最常见的原因就是写侧没投影成功）
                _sink = self._make_trace_sink(request, "GRAPH_PROJECT")
                if _sink is not None:
                    _st = dict(_proj_stats or {})
                    _st["superseded_synced"] = len(superseded_old_ids or [])
                    await _sink(
                        parsed=_st,
                        response=json.dumps(_st, ensure_ascii=False, default=str),
                        memory_ids=[
                            getattr(n, "node_id", "") for n in nodes
                            if getattr(n, "node_id", "")
                        ],
                        elapsed_ms=(datetime.now() - _t_proj).total_seconds() * 1000,
                    )
                await self._maybe_topic_summaries(nodes, request)
            except Exception as e:
                logger.warning(f"[graph] projection failed (non-fatal): {e}")

        async def _run() -> None:
            provider_obj = getattr(
                getattr(self.config, "graph_store", None), "provider", ""
            )
            provider = str(getattr(provider_obj, "value", provider_obj) or "").lower()
            async with graph_slot(kuzu=(provider == "kuzu")):
                await _run_unbounded()

        try:
            import asyncio
            task = asyncio.get_running_loop().create_task(_run())
            if request is not None:
                # Request-local handle: a finalize for this exact write may wait for
                # its typed edges without coupling concurrent users to one global task.
                setattr(request, "_graph_projection_task", task)
            return task
        except RuntimeError:
            # 没有 running loop（同步上下文）→ 直接跳过，不为图阻塞主流程
            logger.debug("[graph] no running loop, projection skipped")
            return None

    @staticmethod
    async def _await_graph_projection(request: Optional[WriteRequest]) -> None:
        """Join a request's graph work exactly once before returning."""
        if request is None:
            return
        task = getattr(request, "_graph_projection_task", None)
        if task is None:
            return
        try:
            await task
        except Exception as exc:
            logger.warning("[graph] projection join failed (non-fatal): %s", exc)
        finally:
            try:
                delattr(request, "_graph_projection_task")
            except AttributeError:
                pass

    def _attach_graph_executor_diagnostics(self, response: WriteResponse) -> None:
        snapshot = getattr(self._graph_store, "executor_snapshot", None)
        if callable(snapshot):
            response.extra["graph_executor"] = snapshot()

    async def _maybe_topic_summaries(
        self, nodes: List[Any], request: Optional[WriteRequest] = None,
    ) -> None:
        """按需刷新 Topic-scoped summary（按主题切摘要，而非按时间窗）。

        默认关（`MEMORY_TOPIC_SUMMARY_ENABLED`）—— 它要额外的 LLM 调用。
        整段包在 try 里：摘要是附加产物，失败不影响记忆本体。
        """
        gcfg = getattr(self.config, "graph_store", None)
        if gcfg is None or not getattr(gcfg, "topic_summary_enabled", False):
            return
        graph_store = getattr(self, "_graph_store", None)
        if graph_store is None or not nodes:
            return
        try:
            from ._graph.topic_summary import maybe_summarize_topics

            first = nodes[0]
            isolation_key = first.get_isolation_key()
            # 本次写入触达了哪些 Topic：按 tag 名反查 topic_id
            tag_names = {
                str(t) for n in nodes for t in (getattr(n, "tags", None) or []) if t
            }
            if not tag_names:
                return
            topic_ids = [
                t["topic_id"]
                for t in await graph_store.get_all_topics(
                    MemoryNode.topic_scope_of(isolation_key)
                )
                if t.get("name") in tag_names and t.get("topic_id")
            ]
            if not topic_ids:
                return

            await maybe_summarize_topics(
                graph_store=graph_store,
                vector_store=await self._get_vector_store(),
                embed_service=self.embed_service,
                llm_provider=self.reconciler._get_llm(),
                user_id=getattr(first, "user_id", ""),
                agent_id=getattr(first, "agent_id", None),
                session_id=getattr(first, "session_id", ""),
                isolation_key=isolation_key,
                touched_topic_ids=topic_ids,
                min_memories=getattr(gcfg, "topic_summary_min_memories", 5),
                step=getattr(gcfg, "topic_summary_step", 5),
                max_concurrency=getattr(gcfg, "topic_summary_concurrency", 3),
                trace=(
                    self._make_trace_sink(request, "TOPIC_SUMMARY")
                    if request is not None else None
                ),
            )
        except Exception as e:
            logger.warning(f"[topic-summary] failed (non-fatal): {e}")

    async def _reconcile_and_store(
        self,
        new_memory_texts: List[str],
        new_memories_meta: List[Dict],
        request: WriteRequest,
        vector_store: VectorStoreBase,
        req_id: str,
        source_raw_memory_id: str = "",
        precomputed_embeddings: Optional[Dict[str, List[float]]] = None,
    ) -> Tuple[List[str], Optional[str], Dict[str, int], Dict[str, int]]:
        """Serialize reconcile/apply per user, preserving timeline semantics."""
        async with _reconcile_user_lock(request.user_id or "default"):
            return await self._reconcile_and_store_locked(
                new_memory_texts=new_memory_texts,
                new_memories_meta=new_memories_meta,
                request=request,
                vector_store=vector_store,
                req_id=req_id,
                source_raw_memory_id=source_raw_memory_id,
                precomputed_embeddings=precomputed_embeddings,
            )

    async def _reconcile_and_store_locked(
        self,
        new_memory_texts: List[str],
        new_memories_meta: List[Dict],
        request: WriteRequest,
        vector_store: VectorStoreBase,
        req_id: str,
        source_raw_memory_id: str = "",
        precomputed_embeddings: Optional[Dict[str, List[float]]] = None,
    ) -> Tuple[List[str], Optional[str], Dict[str, int], Dict[str, int]]:
        """
        对新 memories 做 reconcile，执行 ADD（含 EVOLVE），写 WRITE_SUMMARY log。

        Returns:
            (stored_ids, error_message, ops_counts, recon_tokens)
            error_message=None 表示成功；ops_counts 含 add/supersede/update/total；
            recon_tokens 含 prompt/completion/total（reconcile 链路 LLM 消耗）
        """
        stored_ids: List[str] = []
        # Episode：本次写入归属的内部会话 id（跨 write 把"一件事"归到一起）。
        # 只数 user 侧记忆（与 rolling summary 只数 pending_user_count 一致）——
        # agent 侧记忆若参与计数会把 episode 提前撑到上限、切得过碎，白付摘要的 token。
        episode_id = await self._resolve_episode_id(
            request, _count_user_owned(new_memories_meta)
        )
        session_episode_id = self._session_episode_id(request)
        if session_episode_id:
            episode_id = session_episode_id
        # 关系图用：收集本次落库的 (node, op)，供 graph 投影建边。
        # 收 op 是为了拿 op.related（LLM 标注的 typed 关系）。
        graph_payload: List[Tuple[Any, Any]] = []
        superseded_old_ids: List[str] = []
        tags_to_index: set[str] = set()
        current_time = request.memory_at.isoformat(timespec="seconds") if request.memory_at else ""

        recon_result = await self.reconciler.reconcile(
            new_memories=new_memory_texts,
            user_id=request.user_id,
            agent_id=request.agent_id or "default_agent",
            vector_store=vector_store,
            embed_service=self.embed_service,
            layers=[MemoryLayer.L2_FACT, MemoryLayer.L4_IDENTITY],
            cache=self._cache,
            request_id=req_id,
            current_time=current_time,
            new_memories_with_meta=new_memories_meta,
            session_id=request.session_id or "",
            graph_store=getattr(self, "_graph_store", None),
            new_memory_embeddings=precomputed_embeddings,
            source=request.source,
        )

        if not recon_result.success:
            return [], recon_result.error, {"add": 0, "supersede": 0, "update": 0, "total": 0}, {"prompt": 0, "completion": 0, "total": 0}

        # 分类统计
        _op_counts = {}
        for op in recon_result.ops:
            _op_counts[op.op] = _op_counts.get(op.op, 0) + 1
        _op_counts_str = " ".join(f"{k}={v}" for k, v in sorted(_op_counts.items()))

        logger.info(
            f"TRACE_PERF [{req_id}] S1_RECONCILE_DONE "
            f"ops={len(recon_result.ops)} candidates={len(new_memory_texts)} | {_op_counts_str}"
            + (f" | DEGRADED({recon_result.degraded_reason})"
               if getattr(recon_result, "degraded", False) else "")
        )
        if getattr(recon_result, "degraded", False):
            # 走了 ADD-all 兜底：内容保住了，但没做去重/演化判定。必须可观测，
            # 否则线上只会看到"记忆变多了"而不知道为什么。
            logger.warning(
                f"[write] reconcile degraded → ADD-all "
                f"({recon_result.degraded_reason}), {len(recon_result.ops)} memories stored as-is"
            )
        # 逐条 op 详情由 reconciler 的 "[reconciler] ops detail" 单条 JSON list 输出，
        # 这里不再逐条打印（避免重复刷屏）。

        # ── 批量 embed：收集所有需要 embed 的 content，一次性 batch 调用 ──
        contents_to_embed: List[str] = []
        content_indices: List[int] = []  # 对应 ops 索引
        for i, op in enumerate(recon_result.ops):
            if op.op in ("SUPERSEDE", "UPDATE"):
                if op.op == "UPDATE" and not op.content:
                    continue  # shadow-only, no embed needed
                if not op.memory_id:
                    continue
                contents_to_embed.append(op.content or "")
                content_indices.append(i)
            elif op.op == "ADD":
                contents_to_embed.append(op.content or "")
                content_indices.append(i)

        # 一次 batch embed（不逐个串行）
        embeddings_map: Dict[int, List[float]] = {}
        reconciled_embeddings = getattr(
            recon_result, "new_memory_embeddings", None,
        ) or {}
        # 仅复用 reconcile 输入原文的向量。LLM 若把内容改写成 UPDATE/SUPERSEDE，
        # content 不同便不会命中，仍按新内容计算 embedding。
        for idx, content in zip(content_indices, contents_to_embed):
            emb = reconciled_embeddings.get(content)
            if emb:
                embeddings_map[idx] = emb

        missing_pairs = [
            (idx, content)
            for idx, content in zip(content_indices, contents_to_embed)
            if idx not in embeddings_map
        ]
        if missing_pairs:
            try:
                batch_embeddings = await self.embed_service.embed_batch(
                    [content for _, content in missing_pairs]
                )
                for (idx, _), emb in zip(missing_pairs, batch_embeddings):
                    embeddings_map[idx] = emb
            except Exception as e:
                logger.warning(f"[reconciler] batch embed failed, falling back to sequential: {e}")
                # fallback: 逐个 embed
                for idx, content in missing_pairs:
                    try:
                        embeddings_map[idx] = await self.embed_service.embed_queued(content)
                    except Exception as e2:
                        logger.error(f"[reconciler] embed failed for op {idx}: {e2}")

        # 统计
        add_cnt = 0           # ADD（全新）
        supersede_cnt = 0     # SUPERSEDE（矛盾演化）
        update_cnt = 0        # UPDATE（合并精炼）

        # A fresh-user reconcile commonly produces an ADD-only batch (PMV2
        # chunks often contain 7-15 facts).  Every vector-store backend already
        # supports upsert_batch, but the writer historically sent one network
        # request per fact.  Batch only the dependency-free ADD-only case;
        # UPDATE/SUPERSEDE keep their original ordered application semantics.
        ops_to_apply = list(enumerate(recon_result.ops))
        batch_update = getattr(vector_store, "update_payload_batch", None)
        if (
            len(recon_result.ops) >= 2
            and all(
                op.op == "UPDATE" and op.memory_id and op.content
                for op in recon_result.ops
            )
            and not getattr(request, "client_meta", None)
            and callable(batch_update)
        ):
            update_rows = []
            update_nodes: List[Tuple[int, Any, Any]] = []
            # UPDATE refines one durable memory in place. Its logical event time
            # belongs to the original memory and must not jump to the latest write
            # merely because another occurrence/detail was reconciled into it.
            # Fetch all targets once so graph projection uses the same preserved
            # timestamp. If a legacy node has no time, the new known time may fill it.
            existing_update_nodes: Dict[str, MemoryNode] = {}
            try:
                existing_update_nodes = {
                    node.node_id: node
                    for node in await vector_store.get_by_ids([
                        op.memory_id for op in recon_result.ops if op.memory_id
                    ])
                    if node is not None
                }
            except Exception as e:
                logger.warning(
                    "[reconciler] batch UPDATE target lookup failed; "
                    "preserving stored memory_at implicitly: %s", e,
                )
            for op_idx, op in enumerate(recon_result.ops):
                content = op.content or ""
                layer = (
                    MemoryLayer.from_string(op.layer)
                    if op.layer else MemoryLayer.L2_FACT
                )
                updates = {
                    "content": content,
                    "source": request.source,
                    "embedding": (
                        embeddings_map.get(op_idx)
                        or await self.embed_service.embed_queued(content)
                    ),
                    "tags": list(op.tags or []),
                    "tag_ners": list(getattr(op, "tag_ners", None) or []),
                    "layer": layer.value,
                    "gmt_modified": int(datetime.now().timestamp()),
                    "is_latest": True,
                    "status": MemoryStatus.ACTIVE.value,
                }
                old_node = existing_update_nodes.get(op.memory_id)
                if (
                    old_node is not None
                    and old_node.memory_at is None
                    and request.memory_at is not None
                ):
                    updates["memory_at"] = int(request.memory_at.timestamp())
                if source_raw_memory_id:
                    updates["source_raw_memory_id"] = source_raw_memory_id
                if session_episode_id:
                    updates["episode_id"] = (
                        getattr(old_node, "episode_id", "") or session_episode_id
                    )
                merged_custom = self._merge_source_raw_ids(
                    old_node, source_raw_memory_id,
                )
                if getattr(
                    getattr(self.config, "llm", None),
                    "structure_metadata_enabled", False,
                ):
                    merged_custom = merge_structural_custom(
                        merged_custom,
                        topic_paths=getattr(op, "topic_paths", None),
                        event_facet=getattr(op, "event_facet", None),
                        topic_assignments=getattr(op, "topic_assignments", None),
                        topic_paths_provided=bool(
                            getattr(op, "topic_paths_provided", False)
                            or getattr(op, "topic_paths", None)
                        ),
                        event_facet_provided=bool(
                            getattr(op, "event_facet_provided", False)
                            or getattr(op, "event_facet", None)
                        ),
                        topic_assignments_provided=bool(
                            getattr(op, "topic_assignments_provided", False)
                            or getattr(op, "topic_assignments", None)
                        ),
                    )
                if session_episode_id:
                    merged_custom = add_parent_episode_membership(
                        merged_custom, session_episode_id,
                        similarity=1.0, method="write_session",
                    )
                if merged_custom:
                    updates["custom"] = merged_custom
                update_rows.append((op.memory_id, updates))
                update_nodes.append((op_idx, self._graph_stub_node(
                    op.memory_id, content, layer, list(op.tags or []), request,
                    memory_at=(
                        old_node.memory_at
                        if old_node is not None and old_node.memory_at is not None
                        else request.memory_at if old_node is not None else None
                    ),
                    custom=merged_custom,
                ), op))

            updated_ids = set(await batch_update(update_rows) or [])
            for _op_idx, node, op in update_nodes:
                if node.node_id not in updated_ids:
                    logger.warning(
                        "[reconciler] UPDATE batch did not return node id %s",
                        node.node_id,
                    )
                    continue
                stored_ids.append(node.node_id)
                graph_payload.append((node, op))
                update_cnt += 1
                await self._emit_reconcile_apply(
                    request, vector_store=vector_store, op_type="UPDATE",
                    memory_id=node.node_id, layer=node.layer.value,
                    content=node.content,
                )
                tags_to_index.update(t for t in (op.tags or []) if t)
                if self._cache:
                    try:
                        await self._cache.store_memory_operation(
                            request_id=req_id,
                            user_id=request.user_id,
                            agent_id=request.agent_id or "default_agent",
                            op="UPDATE",
                            memory_id=node.node_id,
                            content=node.content,
                            layer=node.layer.value,
                            reason=op.reason,
                        )
                    except Exception as e:
                        logger.warning(
                            "[reconciler] store_memory_operation(UPDATE) failed: %s", e,
                        )
            logger.info(
                "TRACE_PERF [%s] S1_UPDATE_BATCH_DONE nodes=%d",
                req_id, len(updated_ids),
            )
            ops_to_apply = []

        if (
            ops_to_apply
            and len(recon_result.ops) >= 2
            and all(op.op == "ADD" for op in recon_result.ops)
            and callable(getattr(vector_store, "upsert_batch", None))
        ):
            add_nodes: List[Tuple[Any, Any]] = []
            for op_idx, op in enumerate(recon_result.ops):
                content = op.content or ""
                layer = (
                    MemoryLayer.from_string(op.layer)
                    if op.layer else MemoryLayer.L2_FACT
                )
                add_nodes.append((MemoryNode(
                    user_id=request.user_id,
                    agent_id=request.agent_id or "default_agent",
                    session_id=request.session_id or "default_session",
                    source=request.source,
                    layer=layer,
                    content=content,
                    episode_id=episode_id,
                    owner=getattr(op, "owner", None),
                    speculate=op.speculate,
                    supersedes=None,
                    is_latest=True,
                    source_type=SourceType.INFERRED,
                    status=MemoryStatus.ACTIVE,
                    embedding=(
                        embeddings_map.get(op_idx)
                        or await self.embed_service.embed_queued(content)
                    ),
                    memory_at=request.memory_at,
                    source_raw_memory_id=source_raw_memory_id or None,
                    tags=list(op.tags or []),
                    tag_ners=list(getattr(op, "tag_ners", None) or []),
                    custom=self._fact_custom(op, req_id, request),
                ), op))

            batch_ids = await vector_store.upsert_batch(
                [node for node, _op in add_nodes]
            )
            accepted = set(batch_ids or [])
            for node, op in add_nodes:
                if node.node_id not in accepted:
                    logger.warning(
                        "[reconciler] ADD batch did not return node id %s",
                        node.node_id,
                    )
                    continue
                stored_ids.append(node.node_id)
                graph_payload.append((node, op))
                add_cnt += 1
                await self._emit_reconcile_apply(
                    request, vector_store=vector_store, op_type="ADD",
                    memory_id=node.node_id, layer=node.layer.value,
                    content=node.content,
                )
                await self._maybe_index_entities(vector_store, node, request)
                tags_to_index.update(t for t in (node.tags or []) if t)
                if self._cache:
                    try:
                        await self._cache.store_memory_operation(
                            request_id=req_id,
                            user_id=request.user_id,
                            agent_id=request.agent_id or "default_agent",
                            op="ADD",
                            memory_id=node.node_id,
                            content=node.content,
                            layer=node.layer.value,
                            reason=op.reason,
                            supersedes=[],
                        )
                    except Exception as e:
                        logger.warning(
                            "[reconciler] store_memory_operation failed: %s", e,
                        )
            logger.info(
                "TRACE_PERF [%s] S1_ADD_BATCH_DONE nodes=%d",
                req_id, len(stored_ids),
            )
            ops_to_apply = []

        for op_idx, op in ops_to_apply:
            # ------------------------------------------------
            # SUPERSEDE / UPDATE：标记旧节点 → 创建新节点
            # SUPERSEDE: 旧节点 status=SUPERSEDED（进演化链，仍可召回+展开），
            #            新节点 supersedes=[old_id]
            # UPDATE: 旧节点 status=SHADOW（逻辑删除，不召回），
            #         新节点 supersedes=None（不进链）
            # ------------------------------------------------
            if op.op in ("SUPERSEDE", "UPDATE"):
                target_id = op.memory_id
                if not target_id:
                    logger.warning(f"[reconciler] {op.op} op missing memory_id, skipped")
                    continue

                # UPDATE with no content = legacy DELETE mapping (shadow-only)
                if op.op == "UPDATE" and not op.content:
                    try:
                        await vector_store.update_payload(
                            target_id,
                            {
                                "is_latest": False,
                                "status": MemoryStatus.SHADOW.value,
                            },
                        )
                        try:
                            await vector_store.delete_retrieval_clauses(target_id)
                        except Exception as clause_error:
                            logger.debug(
                                "[retrieval-clause] shadow cleanup failed for %s: %s",
                                target_id, clause_error,
                            )
                        logger.debug(f"[reconciler] shadow-only UPDATE: memory_id={target_id}")
                    except Exception as e:
                        logger.warning(f"[reconciler] failed to shadow node {target_id}: {e}")
                    continue

                content = op.content or ""
                layer = MemoryLayer.from_string(op.layer) if op.layer else MemoryLayer.L2_FACT

                # SUPERSEDE → 旧节点进演化链，status=SUPERSEDED（仍可被召回，
                #   命中后双向展开整链）；新节点 supersedes=[old_id]。
                # UPDATE → 旧节点不进链，status=SHADOW（等价逻辑删除，不召回）；
                #   新节点 supersedes=None，独立。
                is_supersede = op.op == "SUPERSEDE"
                old_status = (
                    MemoryStatus.SUPERSEDED.value if is_supersede
                    else MemoryStatus.SHADOW.value
                )

                # ------------------------------------------------
                # 多节点链折叠（仅 SUPERSEDE）：
                # op.memory_ids = [E0, E1, ...]（有序，旧→新）。把这些原本
                # 未成链的旧节点先连成一条链（E0 ← E1 ← ...），再让新 head
                # 节点 supersede 最新的那个（target_id = memory_ids[-1]）。
                # 每条旧节点都被标 SUPERSEDED（仍可召回+展开）；只有 head（新节点）
                # 保持 is_latest=True。
                # ------------------------------------------------
                chain_ids = list(getattr(op, "memory_ids", None) or [])
                if is_supersede and len(chain_ids) > 1:
                    for _prev_id, _cur_id in zip(chain_ids, chain_ids[1:]):
                        # _cur_id supersedes _prev_id：建立 supersedes / superseded_by 双向链
                        try:
                            _cur_node = await vector_store.get_by_id(_cur_id)
                            if _cur_node is not None:
                                _sup = list(_cur_node.supersedes or [])
                                if _prev_id not in _sup:
                                    _sup.append(_prev_id)
                                await vector_store.update_payload(
                                    _cur_id, {"supersedes": _sup}
                                )
                        except Exception as e:
                            logger.warning(
                                f"[reconciler] chain-link supersedes on {_cur_id} failed: {e}"
                            )
                        try:
                            _prev_node = await vector_store.get_by_id(_prev_id)
                            _sb = list(_prev_node.superseded_by or []) if _prev_node else []
                            if _cur_id not in _sb:
                                _sb.append(_cur_id)
                            await vector_store.update_payload(
                                _prev_id,
                                {
                                    "superseded_by": _sb,
                                    "is_latest": False,
                                    "status": MemoryStatus.SUPERSEDED.value,
                                },
                            )
                        except Exception as e:
                            logger.warning(
                                f"[reconciler] chain-link superseded_by on {_prev_id} failed: {e}"
                            )
                    logger.debug(
                        f"[reconciler] SUPERSEDE chain fold: {chain_ids} "
                        f"(head target={target_id})"
                    )

                # 标记链上最新的 target_id（新 head 直接取代它）：
                # SUPERSEDE → SUPERSEDED（进链，可召回）；UPDATE → SHADOW（逻辑删除）
                # ------------------------------------------------
                # UPDATE（有 content，非 SUPERSEDE）：原地更新旧节点
                # ------------------------------------------------
                # UPDATE 语义是「合并精炼同一主题」，本质是同一条记忆的延续，因此
                # 原地更新 target_id：换 content + embedding + tags；保留原始
                # logical memory_at。UPDATE 是同一 durable memory 的精炼或多次事件
                # 聚合，不能把整条历史的时间覆盖成本次 write 时间。
                # 天然保留 access_count / last_accessed_at（不打回冷启动），
                # 也不产生 SHADOW 垃圾节点。SUPERSEDE 仍走下面的 shadow+建新链。
                if not is_supersede:
                    new_emb = embeddings_map.get(op_idx) or await self.embed_service.embed_queued(content)
                    _old = None
                    _old_lookup_ok = False
                    try:
                        _old = await vector_store.get_by_id(target_id)
                        _old_lookup_ok = True
                    except Exception as e:
                        logger.warning(
                            f"[reconciler] read UPDATE target {target_id} failed; "
                            f"preserving stored memory_at implicitly: {e}"
                        )
                    _updates = {
                        "content": content,
                        "source": request.source,
                        "embedding": new_emb,
                        "tags": list(op.tags or []),
                        "tag_ners": list(getattr(op, "tag_ners", None) or []),
                        "layer": layer.value,
                        "gmt_modified": int(datetime.now().timestamp()),
                        "is_latest": True,
                        "status": MemoryStatus.ACTIVE.value,
                    }
                    if (
                        _old_lookup_ok
                        and _old is not None
                        and _old.memory_at is None
                        and request.memory_at is not None
                    ):
                        _updates["memory_at"] = int(request.memory_at.timestamp())
                    if getattr(op, "owner", None):
                        _updates["owner"] = _norm_owner(op.owner)
                    if source_raw_memory_id:
                        _updates["source_raw_memory_id"] = source_raw_memory_id
                    if session_episode_id:
                        _updates["episode_id"] = (
                            getattr(_old, "episode_id", "") or session_episode_id
                        )
                    try:
                        _merged_sources = self._merge_source_raw_ids(
                            _old, source_raw_memory_id,
                        )
                        if getattr(
                            getattr(self.config, "llm", None),
                            "structure_metadata_enabled", False,
                        ):
                            _merged_sources = merge_structural_custom(
                                _merged_sources,
                                topic_paths=getattr(op, "topic_paths", None),
                                event_facet=getattr(op, "event_facet", None),
                                topic_assignments=getattr(op, "topic_assignments", None),
                                topic_paths_provided=bool(
                                    getattr(op, "topic_paths_provided", False)
                                    or getattr(op, "topic_paths", None)
                                ),
                                event_facet_provided=bool(
                                    getattr(op, "event_facet_provided", False)
                                    or getattr(op, "event_facet", None)
                                ),
                                topic_assignments_provided=bool(
                                    getattr(op, "topic_assignments_provided", False)
                                    or getattr(op, "topic_assignments", None)
                                ),
                            )
                        if _merged_sources:
                            _updates["custom"] = _merged_sources
                    except Exception as e:
                        logger.warning(
                            f"[reconciler] merge source provenance on {target_id} failed: {e}"
                        )
                    # merge 语义：UPDATE 原地改同一个节点，本次 meta 必须追加到旧
                    # meta list 上。custom 是整体替换的，所以要先回读再写回。
                    # 仅在本次带了 meta 时才多这一次读，否则维持原有单次写。
                    if getattr(request, "client_meta", None):
                        try:
                            _merged = self._merge_client_meta(
                                getattr(_old, "custom", None), request,
                            )
                            if _merged is None:
                                _merged = dict(_updates.get("custom") or getattr(_old, "custom", None) or {})
                            elif _updates.get("custom"):
                                # `_merged` owns the newly appended client meta;
                                # `_updates.custom` owns fresh provenance and
                                # structural fields. Preserve both, with the new
                                # structure/source values taking precedence.
                                _incoming = dict(_updates["custom"])
                                _incoming.pop("meta", None)
                                _merged.update(_incoming)
                            if session_episode_id:
                                _merged = add_parent_episode_membership(
                                    _merged, session_episode_id,
                                    similarity=1.0, method="write_session",
                                )
                            if _merged is not None:
                                _updates["custom"] = _merged
                        except Exception as e:
                            # meta 是附加负载，回读失败不该阻塞记忆本体更新
                            logger.warning(
                                f"[reconciler] merge client meta on {target_id} failed: {e}"
                            )
                    elif session_episode_id:
                        _updates["custom"] = add_parent_episode_membership(
                            _updates.get("custom") or getattr(_old, "custom", None),
                            session_episode_id,
                            similarity=1.0, method="write_session",
                        )
                    try:
                        await vector_store.update_payload(target_id, _updates)
                    except Exception as e:
                        logger.warning(f"[reconciler] in-place UPDATE failed on {target_id}: {e}")
                        continue
                    stored_ids.append(target_id)
                    graph_payload.append((
                        self._graph_stub_node(
                            target_id, content, layer, list(op.tags or []), request,
                            memory_at=(
                                _old.memory_at
                                if _old is not None and _old.memory_at is not None
                                else request.memory_at if _old_lookup_ok else None
                            ),
                            custom=_updates.get("custom") or getattr(_old, "custom", None),
                        ),
                        op,
                    ))
                    update_cnt += 1
                    await self._emit_reconcile_apply(
                        request, vector_store=vector_store, op_type="UPDATE",
                        memory_id=target_id, layer=layer.value, content=content,
                    )
                    logger.debug(
                        f"[reconciler] UPDATE(in-place): {content[:80]} → node_id={target_id} "
                        f"layer={layer.value} reason={op.reason}"
                    )
                    tags_to_index.update(t for t in (op.tags or []) if t)
                    # 写 memory_operations 记录
                    if self._cache:
                        try:
                            await self._cache.store_memory_operation(
                                request_id=req_id,
                                user_id=request.user_id,
                                agent_id=request.agent_id or "default_agent",
                                op=op.op,
                                memory_id=target_id,
                                content=content,
                                layer=layer.value,
                                reason=op.reason,
                            )
                        except Exception as e:
                            logger.warning(f"[reconciler] store_memory_operation(UPDATE) failed: {e}")
                    continue

                # ------------------------------------------------
                # SUPERSEDE：shadow（SUPERSEDED）旧节点 + 建新链头节点
                # ------------------------------------------------
                try:
                    await vector_store.update_payload(
                        target_id,
                        {
                            "is_latest": False,
                            "status": old_status,
                        },
                    )
                    try:
                        await vector_store.delete_retrieval_clauses(target_id)
                    except Exception as clause_error:
                        logger.debug(
                            "[retrieval-clause] supersede cleanup failed for %s: %s",
                            target_id, clause_error,
                        )
                except Exception as e:
                    logger.warning(f"[reconciler] failed to mark old node {target_id} ({op.op}): {e}")
                    continue

                # 创建新 head 节点（此处只可能是 SUPERSEDE；UPDATE 已在上面原地更新并 continue）
                new_node = MemoryNode(
                    user_id=request.user_id,
                    agent_id=request.agent_id or "default_agent",
                    session_id=request.session_id or "default_session",
                    source=request.source,
                    layer=layer,
                    content=content,
                    episode_id=episode_id,
                    owner=getattr(op, "owner", None),
                    speculate=op.speculate,
                    supersedes=[target_id],
                    is_latest=True,
                    source_type=SourceType.INFERRED,
                    status=MemoryStatus.ACTIVE,
                    embedding=embeddings_map.get(op_idx) or await self.embed_service.embed_queued(content),
                    memory_at=request.memory_at,
                    source_raw_memory_id=source_raw_memory_id or None,
                    tags=list(op.tags or []),
                    tag_ners=list(getattr(op, "tag_ners", None) or []),
                    # SUPERSEDE 建的是新节点，只带本次 meta：meta 的语义是「该节点被
                    # 写入时的上下文」，而被取代的旧节点仍在库里、其 meta 原样保留，
                    # 沿演化链可查，不会丢。若改成继承整链 meta，每次取代都会累积，
                    # 长链下极易撞 5KB 上限。
                    custom=self._fact_custom(op, req_id, request),
                )
                nid = await vector_store.upsert(new_node)
                stored_ids.append(nid)
                graph_payload.append((new_node, op))
                if target_id:
                    superseded_old_ids.append(target_id)
                await self._emit_reconcile_apply(
                    request, vector_store=vector_store, op_type="SUPERSEDE",
                    memory_id=nid, layer=layer.value, content=content,
                    supersedes=[target_id],
                )
                await self._maybe_index_entities(vector_store, new_node, request)

                supersede_cnt += 1

                logger.debug(
                    f"[reconciler] SUPERSEDE: {content[:80]} → node_id={nid} "
                    f"layer={layer.value} supersedes=[{target_id}] reason={op.reason}"
                )

                # SUPERSEDE: 回写旧节点的 superseded_by（建立链关系）
                try:
                    old_node = await vector_store.get_by_id(target_id)
                    existing_superseded_by = (old_node.superseded_by or []) if old_node else []
                    if nid not in existing_superseded_by:
                        existing_superseded_by.append(nid)
                    await vector_store.update_payload(
                        target_id,
                        {"superseded_by": existing_superseded_by},
                    )
                except Exception as e:
                    logger.warning(f"[reconciler] failed to update superseded_by on {target_id}: {e}")

                tags_to_index.update(t for t in (new_node.tags or []) if t)

                # 写 memory_operations 记录
                if self._cache:
                    try:
                        await self._cache.store_memory_operation(
                            request_id=req_id,
                            user_id=request.user_id,
                            agent_id=request.agent_id or "default_agent",
                            op=op.op,
                            memory_id=nid,
                            content=content,
                            layer=layer.value,
                            reason=op.supersede_reason or op.reason,
                            supersedes=[target_id] if is_supersede else [],
                        )
                    except Exception as e:
                        logger.warning(f"[reconciler] store_memory_operation({op.op}) failed: {e}")
                continue

            # ------------------------------------------------
            # ADD op：纯新增节点
            # ------------------------------------------------
            if op.op != "ADD":
                logger.warning(f"[reconciler] unknown op '{op.op}', skipped")
                continue

            content = op.content or ""
            layer = MemoryLayer.from_string(op.layer) if op.layer else MemoryLayer.L2_FACT

            new_node = MemoryNode(
                user_id=request.user_id,
                agent_id=request.agent_id or "default_agent",
                session_id=request.session_id or "default_session",
                source=request.source,
                layer=layer,
                content=content,
                episode_id=episode_id,
                owner=getattr(op, "owner", None),
                speculate=op.speculate,
                supersedes=None,
                is_latest=True,
                source_type=SourceType.INFERRED,
                status=MemoryStatus.ACTIVE,
                embedding=embeddings_map.get(op_idx) or await self.embed_service.embed_queued(content),
                memory_at=request.memory_at,
                source_raw_memory_id=source_raw_memory_id or None,
                tags=list(op.tags or []),
                tag_ners=list(getattr(op, "tag_ners", None) or []),
                custom=self._fact_custom(op, req_id, request),
            )
            nid = await vector_store.upsert(new_node)
            stored_ids.append(nid)
            graph_payload.append((new_node, op))
            add_cnt += 1
            await self._emit_reconcile_apply(
                request, vector_store=vector_store, op_type="ADD",
                memory_id=nid, layer=layer.value, content=content,
            )
            await self._maybe_index_entities(vector_store, new_node, request)
            logger.debug(
                f"[reconciler] ADD: {content[:80]} → node_id={nid} "
                f"layer={layer.value} reason={op.reason}"
            )

            tags_to_index.update(t for t in (new_node.tags or []) if t)

            if self._cache:
                try:
                    await self._cache.store_memory_operation(
                        request_id=req_id,
                        user_id=request.user_id,
                        agent_id=request.agent_id or "default_agent",
                        op="ADD",
                        memory_id=nid,
                        content=content,
                        layer=layer.value,
                        reason=op.reason,
                        supersedes=[],
                    )
                except Exception as e:
                    logger.warning(f"[reconciler] store_memory_operation failed: {e}")

        # 长聚合 L2 的隐藏检索入口。alias 只存在辅助索引，命中后返回 parent L2。
        if graph_payload:
            try:
                await sync_retrieval_clause_index(
                    vector_store=vector_store,
                    embed_service=self.embed_service,
                    nodes=[node for node, _op in graph_payload],
                    enabled=bool(getattr(
                        self.config.recall, "retrieval_clauses_enabled", False
                    )),
                    min_words=int(getattr(
                        self.config.recall, "retrieval_clauses_min_words", 70
                    )),
                    clear_empty=False,
                )
                for node, op in graph_payload:
                    if (
                        getattr(op, "op", "") == "UPDATE"
                        and not eligible_retrieval_clauses(
                            node, min_words=int(getattr(
                            self.config.recall, "retrieval_clauses_min_words", 70
                        )))
                    ):
                        await vector_store.delete_retrieval_clauses(node.node_id)
            except Exception as e:
                logger.warning(
                    "[retrieval-clause] maintain failed (non-fatal): %s", e
                )

        # 一次 write 内跨所有 op 合并 tag，避免每条 memory 各自等待一轮 tag embedding。
        if tags_to_index:
            try:
                await _tag_index_helper.ensure_tag_embeddings_for_node(
                    vector_store=vector_store,
                    embed_service=self.embed_service,
                    user_id=request.user_id,
                    tags=sorted(tags_to_index),
                )
            except Exception as e:
                logger.debug(f"[tag-index] maintain failed (non-fatal): {e}")
            self._remember_existing_tags(
                request.user_id, request.agent_id, tags_to_index,
            )

        # WRITE_SUMMARY log
        if self._cache and recon_result.ops:
            try:
                import json as _json2
                total_ops = len(recon_result.ops)
                summary_data = {
                    "add_count": add_cnt,
                    "supersede_count": supersede_cnt,
                    "update_count": update_cnt,
                    "total_ops": total_ops,
                    "new_memories_input": len(new_memory_texts),
                }
                await self._cache.store_pipeline_log(
                    request_id=req_id,
                    user_id=request.user_id,
                    agent_id=request.agent_id or "default_agent",
                    session_id=request.session_id or "",
                    step="WRITE_SUMMARY",
                    prompt="",
                    response=_json2.dumps(summary_data, ensure_ascii=False),
                    parsed=_json2.dumps(summary_data, ensure_ascii=False),
                    memory_ids=stored_ids,
                )
            except Exception as e:
                logger.warning(f"[write] store WRITE_SUMMARY failed: {e}")

        # ── 关系图投影（pro 模式，enable_graph 才生效）──
        # 异步 fire-and-forget：图是附加信号，不能进写入关键路径。
        self._schedule_graph_projection(
            graph_payload=graph_payload,
            superseded_old_ids=superseded_old_ids,
            candidate_scores=recon_result.candidate_scores,
            request=request,
        )

        ops_counts = {
            "add": add_cnt,
            "supersede": supersede_cnt,
            "update": update_cnt,
            "total": add_cnt + supersede_cnt + update_cnt,
        }
        recon_tokens = {
            "prompt": recon_result.prompt_tokens,
            "completion": recon_result.completion_tokens,
            "total": recon_result.total_tokens,
        }
        return stored_ids, None, ops_counts, recon_tokens

    async def _direct_store(
        self,
        new_memories_meta: List[Dict],
        request: WriteRequest,
        vector_store: VectorStoreBase,
        req_id: str,
        source_raw_memory_id: str = "",
        precomputed_embeddings: Optional[Dict[str, List[float]]] = None,
    ) -> Tuple[List[str], Optional[str], Dict[str, int], Dict[str, int]]:
        """
        跳过 reconcile，直接把 extractor 提取的 memories 插入 VDB。

        通过 RECONCILE_ENABLED=false 激活。适用于 eval 场景：
        不做去重/合并/演化，保留所有提取结果。
        """
        stored_ids: List[str] = []
        session_episode_id = self._session_episode_id(request)

        # 批量 embed
        contents = [m.get("content", "") for m in new_memories_meta if m.get("content")]
        precomputed_embeddings = precomputed_embeddings or {}
        missing_contents = [c for c in contents if c not in precomputed_embeddings]
        computed_embeddings = (
            await self.embed_service.embed_batch(missing_contents)
            if missing_contents else []
        )
        embedding_by_content = dict(precomputed_embeddings)
        embedding_by_content.update(zip(missing_contents, computed_embeddings))

        stored_nodes: List[MemoryNode] = []
        for meta in new_memories_meta:
            content = meta.get("content", "")
            if not content:
                continue

            layer = MemoryLayer.from_string(meta.get("layer", "L2_FACT"))
            tags = meta.get("tags") or []
            speculate = meta.get("speculate")

            new_node = MemoryNode(
                user_id=request.user_id,
                agent_id=request.agent_id or "default_agent",
                session_id=request.session_id or "default_session",
                source=request.source,
                episode_id=session_episode_id,
                layer=layer,
                content=content,
                owner=_norm_owner(meta.get("owner")),
                speculate=speculate,
                is_latest=True,
                source_type=SourceType.INFERRED,
                status=MemoryStatus.ACTIVE,
                embedding=(
                    embedding_by_content.get(content)
                    or await self.embed_service.embed_queued(content)
                ),
                memory_at=request.memory_at,
                source_raw_memory_id=source_raw_memory_id or None,
                tags=list(tags),
                custom={
                    **self._fact_custom(None, req_id, request),
                    **(
                        structural_custom(
                            meta.get("topic_paths"), meta.get("event_facet"),
                            meta.get("topic_assignments"),
                        )
                        if getattr(
                            getattr(self.config, "llm", None),
                            "structure_metadata_enabled", False,
                        ) else {}
                    ),
                },
            )
            nid = await vector_store.upsert(new_node)
            stored_ids.append(nid)
            stored_nodes.append(new_node)
            await self._emit_reconcile_apply(
                request, vector_store=vector_store, op_type="ADD",
                memory_id=nid, layer=layer.value, content=content,
            )
            await self._maybe_index_entities(vector_store, new_node, request)

        if stored_nodes:
            try:
                await sync_retrieval_clause_index(
                    vector_store=vector_store,
                    embed_service=self.embed_service,
                    nodes=stored_nodes,
                    enabled=bool(getattr(
                        self.config.recall, "retrieval_clauses_enabled", False
                    )),
                    min_words=int(getattr(
                        self.config.recall, "retrieval_clauses_min_words", 70
                    )),
                    clear_empty=False,
                )
            except Exception as e:
                logger.warning(
                    "[retrieval-clause] direct-store maintain failed (non-fatal): %s", e
                )
            self._remember_existing_tags(
                request.user_id,
                request.agent_id,
                {tag for node in stored_nodes for tag in (node.tags or []) if tag},
            )

        logger.info(f"[direct_store] stored {len(stored_ids)} nodes (reconcile disabled)")
        n = len(stored_ids)
        return stored_ids, None, {"add": n, "supersede": 0, "update": 0, "total": n}, {"prompt": 0, "completion": 0, "total": 0}

    async def _emit_pipeline_step(
        self,
        request: WriteRequest,
        *,
        step: str,
        parsed: Union[str, Dict[str, Any], List[Any]],
        elapsed_ms: float = 0,
        response: str = "",
        prompt: str = "",
        memory_ids: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> None:
        """环节级 log/trace（经 client hook：文件始终 + DB 可关）。"""
        if not self._cache:
            return
        parsed_str = (
            parsed
            if isinstance(parsed, str)
            else json.dumps(parsed, ensure_ascii=False, default=str)
        )
        try:
            await self._cache.store_pipeline_log(
                request_id=request.request_id or get_request_id(),
                user_id=request.user_id,
                agent_id=request.agent_id or "default_agent",
                session_id=request.session_id or "",
                step=step,
                prompt=prompt,
                response=response,
                parsed=parsed_str,
                memory_ids=memory_ids,
                elapsed_ms=elapsed_ms,
                **kwargs,
            )
        except Exception as e:
            logger.debug(f"[write] pipeline step {step} failed: {e}")

    async def _emit_reconcile_apply(
        self,
        request: WriteRequest,
        *,
        vector_store,
        op_type: str,
        memory_id: str,
        layer: str,
        content: str,
        supersedes: Optional[List[str]] = None,
    ) -> None:
        """reconcile apply 级 pipeline log（RECONCILE_APPLY）。

        upsert/update_payload 之后立即回读校验该节点是否真落库，把结果写进
        pipeline trace。用于定位「reconcile 决定 ADD、账也记了，但 VDB 里查不到」
        这类问题——区分「没写」「写了没落库」「写后被删」。
        """
        # This read-after-write check is diagnostic, not part of the persistence
        # contract. Running it unconditionally made a normal multi-memory write
        # pay one extra VDB round trip per extracted memory even when trace storage
        # was explicitly disabled. Only perform the stronger read-back audit when
        # the operator has enabled pipeline traces.
        verify_enabled = _write_apply_verify_enabled()
        verified = None
        verify_error = ""
        if verify_enabled:
            try:
                _node = await vector_store.get_by_id(memory_id)
                verified = _node is not None
                if not verified:
                    verify_error = "get_by_id returned None (upsert returned id but not readable)"
            except Exception as e:
                verified = False
                verify_error = f"{type(e).__name__}: {e}"
        parsed = {
            "op": op_type,
            "memory_id": memory_id,
            "layer": layer,
            "content": (content or "")[:200],
            "supersedes": list(supersedes or []),
            "verified": verified,
            "verification_skipped": not verify_enabled,
        }
        if verify_error:
            parsed["verify_error"] = verify_error
        if verify_enabled and not verified:
            logger.warning(
                f"[reconciler] RECONCILE_APPLY verify FAILED op={op_type} "
                f"mid={memory_id} layer={layer}: {verify_error}"
            )
        await self._emit_pipeline_step(
            request,
            step="RECONCILE_APPLY",
            parsed=parsed,
            memory_ids=[memory_id],
        )

    async def _emit_intention_upsert(
        self,
        request: WriteRequest,
        *,
        vector_store,
        memory_id: str,
        content: str,
        tags: Optional[List[str]] = None,
        valid_until=None,
    ) -> None:
        """intention (L7) upsert 级 pipeline log（INTENTION_UPSERT）。

        意图由 System 1 直接 upsert 到 VDB，不走 reconcile，因此不属于 RECONCILE_APPLY。
        这里单独打点：upsert 之后回读校验节点是否真落库，带 memory_id 供线上按
        request_id / memory_id 追查（区分「没写」「写了没落库」「写后被删」）。
        """
        verify_enabled = _write_apply_verify_enabled()
        verified = None
        verify_error = ""
        if verify_enabled:
            try:
                _node = await vector_store.get_by_id(memory_id)
                verified = _node is not None
                if not verified:
                    verify_error = "get_by_id returned None (upsert returned id but not readable)"
            except Exception as e:
                verified = False
                verify_error = f"{type(e).__name__}: {e}"
        parsed = {
            "op": "INTENTION_UPSERT",
            "memory_id": memory_id,
            "layer": MemoryLayer.L7_INTENTION.value,
            "content": (content or "")[:200],
            "tags": list(tags or []),
            "valid_until": valid_until.isoformat() if hasattr(valid_until, "isoformat") else valid_until,
            "verified": verified,
            "verification_skipped": not verify_enabled,
        }
        if verify_error:
            parsed["verify_error"] = verify_error
        if verify_enabled and not verified:
            logger.warning(
                f"[intention] INTENTION_UPSERT verify FAILED mid={memory_id}: {verify_error}"
            )
        await self._emit_pipeline_step(
            request,
            step="INTENTION_UPSERT",
            parsed=parsed,
            memory_ids=[memory_id],
        )

    async def _emit_write_timeline(
        self,
        request: WriteRequest,
        tracer: PipelineTracer,
    ) -> None:
        """请求级 timeline 写入 Trace（SQLite），供 Inspector。"""
        if not self._cache or not is_pipeline_trace_enabled():
            return
        try:
            await self._cache.store_pipeline_log(
                request_id=request.request_id or get_request_id() or tracer.request_id,
                user_id=request.user_id,
                agent_id=request.agent_id or "default_agent",
                session_id=request.session_id or "",
                step="WRITE_TIMELINE",
                prompt="",
                response=tracer.to_summary_line(),
                parsed=json.dumps(tracer.to_dict(), ensure_ascii=False, default=str),
                elapsed_ms=0,
            )
        except Exception as e:
            logger.debug(f"[write] WRITE_TIMELINE failed: {e}")

    # ================================================================
    # 已抽取事实直写流程（跳过 extract，直接 reconcile）
    # ================================================================

    async def write_extracted(
        self,
        request: WriteRequest,
        ctx: Optional[PipelineContext] = None,
        tracer: Optional[PipelineTracer] = None,
    ) -> WriteResponse:
        """写入「agent 已抽取好的」记忆：跳过 extract，直接进 reconcile。

        适用于调用方（如 openclaw memory_add tool）已经把对话抽象成一条/多条
        结论性 memory content 的场景。跳过 LLM extract（省一次 LLM 调用），
        但仍走完整 reconcile：与已有记忆判重 / 合并 / 矛盾演化，并落 L2_FACT。

        与 write() 的区别：
        - 不写 L1_RAW 原始节点（content 本身即事实，无原始对话可留档）。
        - 不调 extractor（mem_agent.process_add）。
        - 直接把 content 列表喂给 _reconcile_and_store（或 RECONCILE_ENABLED=false
          时的 _direct_store）。

        输入约定：request.extra["extracted_contents"] = List[str]（每条一个事实）。
        为空时回退到 request.content（单条）。layer 统一 L2_FACT。
        """
        start_time = datetime.now()
        response = WriteResponse()

        if not request.request_id:
            request.request_id = get_request_id()

        if tracer is None:
            tracer = create_tracer(
                operation="write_extracted",
                pipeline_version="system1",
                uid=request.user_id,
                agent_id=request.agent_id,
                request_id=request.request_id,
                content_preview=request.content,
            )

        try:
            # 收集已抽取事实：优先 extra["extracted_contents"]，否则回退 content
            raw_contents = request.extra.get("extracted_contents")
            if not raw_contents:
                raw_contents = [request.content] if request.content else []
            contents = [c.strip() for c in raw_contents if isinstance(c, str) and c.strip()]

            if not contents:
                response.error_code = 400
                response.error_message = "extracted_contents or content is required"
                return response
            if not request.user_id:
                response.error_code = 400
                response.error_message = "user_id is required"
                return response

            if request.memory_at is None:
                request.memory_at = start_time

            _req_id = request.request_id or get_request_id()
            vector_store = await self._get_vector_store()

            # 直接构造 reconcile 输入（跳过 extract）。layer 统一 L2_FACT。
            new_memory_texts: List[str] = list(contents)
            new_memories_meta: List[Dict] = [
                {"content": c, "layer": "L2_FACT", "tags": []} for c in contents
            ]

            # 抽取好的多条之间互相判重；这里算出的向量继续透传给 reconcile / store。
            precomputed_embeddings: Dict[str, List[float]] = {}
            if len(new_memory_texts) >= 2:
                try:
                    (
                        new_memory_texts,
                        new_memories_meta,
                        precomputed_embeddings,
                    ) = await self._dedup_extracted(
                        new_memory_texts, new_memories_meta, request, _req_id,
                    )
                except Exception as e:
                    logger.warning(f"[write_extracted] dedup failed (non-fatal): {e}")

            _t_recon = datetime.now()
            if _RECONCILE_ENABLED:
                stored_ids, recon_error, ops_counts, recon_tokens = await self._reconcile_and_store(
                    new_memory_texts=new_memory_texts,
                    new_memories_meta=new_memories_meta,
                    request=request,
                    vector_store=vector_store,
                    req_id=_req_id,
                    precomputed_embeddings=precomputed_embeddings,
                )
            else:
                stored_ids, recon_error, ops_counts, recon_tokens = await self._direct_store(
                    new_memories_meta=new_memories_meta,
                    request=request,
                    vector_store=vector_store,
                    req_id=_req_id,
                    precomputed_embeddings=precomputed_embeddings,
                )
            _recon_ms = (datetime.now() - _t_recon).total_seconds() * 1000

            response.extra["reconcile_ops"] = ops_counts
            response.extra["reconcile_tokens"] = recon_tokens
            response.extra["extract_tokens"] = {"prompt": 0, "completion": 0, "total": 0}

            if recon_error is not None:
                response.success = False
                response.error_code = 502
                response.error_message = f"[RECONCILE_FAILED] {recon_error}"
                response.extra["agent_status"] = "failed"
                response.extra["agent_error"] = recon_error
                response.extra["agent_nodes"] = 0
                return response

            response.success = True
            response.layer = MemoryLayer.L2_FACT.value
            response.memory_id = stored_ids[0] if stored_ids else ""
            response.extra["agent_stored_ids"] = stored_ids
            response.extra["agent_status"] = "success"
            response.extra["agent_error"] = ""
            response.extra["agent_nodes"] = len(stored_ids)
            response.extra["agent_mode"] = "extracted"
            response.elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            logger.info(
                f"[write_extracted] done: {len(stored_ids)} nodes stored from "
                f"{len(contents)} extracted contents, recon_ms={_recon_ms:.0f}"
            )
            return response

        except Exception as e:
            logger.error(f"[write_extracted] failed: {e}", exc_info=True)
            response.success = False
            response.error_code = 500
            response.error_message = str(e)
            return response
        finally:
            await self._await_graph_projection(request)
            response.extra["workload"] = workload_snapshot()
            self._attach_graph_executor_diagnostics(response)
            try:
                await self._emit_write_timeline(request, tracer)
            except Exception:
                pass

    # ================================================================
    # 主写入流程
    # ================================================================

    async def write(
        self,
        request: WriteRequest,
        ctx: Optional[PipelineContext] = None,
        tracer: Optional[PipelineTracer] = None,
    ) -> WriteResponse:
        """执行 Lite 写入流程。"""
        start_time = datetime.now()
        response = WriteResponse()

        # request_id 归一化：优先用 client 显式透传的 request.request_id（contextvar-immune），
        # 兜底用 contextvar（保护直接调用 write() 的测试 / 其它 caller）。
        # 此后整条链路落库统一读 request.request_id，不再依赖 contextvar。
        if not request.request_id:
            request.request_id = get_request_id()

        if tracer is None:
            tracer = create_tracer(
                operation="write",
                pipeline_version="system1",
                uid=request.user_id,
                agent_id=request.agent_id,
                request_id=request.request_id,
                content_preview=request.content,
            )

        try:
            # 参数校验
            content = request.content
            if not content and request.has_messages():
                content = request.get_flat_content()
                request.content = content

            if not content:
                response.error_code = 400
                response.error_message = "content or messages is required"
                return response
            if not request.user_id:
                response.error_code = 400
                response.error_message = "user_id is required"
                return response

            # ── 确保 memory_at 有值：未传则用接收到请求的时间 ──
            if request.memory_at is None:
                request.memory_at = start_time

            total_tokens = 0

            # 1. Layer 分配
            with tracer.span("layer_assign") as s:
                layer_str = request.extra.get("layer", "")
                if layer_str:
                    suggested_layer = layer_str
                    s.set_output({"layer": suggested_layer, "source": "explicit"})
                else:
                    suggested_layer = MemoryLayer.L1_RAW.value
                    s.set_output({"layer": suggested_layer, "source": "default_raw"})

            response.layer = suggested_layer

            # ── Timing: sys1_waiting_ms ──
            # start_time 是进入 write() 的时间，现在开始实际 I/O
            _t_process_start = datetime.now()
            _sys1_waiting_ms = (_t_process_start - start_time).total_seconds() * 1000
            _req_id_perf = request.request_id or get_request_id()
            logger.info(f"TRACE_PERF [{_req_id_perf}] S1_START waiting={_sys1_waiting_ms:.0f}ms user={request.user_id}")

            # Metrics: S1 开始
            from ..metrics import MetricsCollector
            MetricsCollector.get().sys1_start()

            # 2. 向量化 + 持久化（L1_RAW）
            _t_embed = datetime.now()
            with tracer.span("embed") as s:
                embedding, embed_meta = await self.embed_service.embed_queued_with_meta(
                    request.content
                )
                s.set_output({"dims": len(embedding), "content_len": len(request.content)})
            _embed_ms = (datetime.now() - _t_embed).total_seconds() * 1000
            response.extra["embedding"] = embedding
            await self._emit_pipeline_step(
                request,
                step="S1_EMBED",
                parsed={
                    "dims": len(embedding),
                    "content_len": len(request.content),
                    "provider_meta": embed_meta,
                },
                elapsed_ms=_embed_ms,
            )

            # 3. 持久化到向量库（L1_RAW）
            _t_l1 = datetime.now()
            memory_id = ""
            _l1_error: Optional[str] = None
            vector_store = None
            mem_node = None
            with tracer.span("qdrant_upsert") as s:
                try:
                    vector_store = await self._get_vector_store()
                    layer_enum = MemoryLayer.from_string(suggested_layer)
                    _raw_custom: Dict[str, Any] = {}
                    self._attach_client_meta(_raw_custom, request)
                    mem_node = MemoryNode(
                        user_id=request.user_id,
                        agent_id=request.agent_id or "default_agent",
                        session_id=request.session_id or "default_session",
                        source=request.source,
                        layer=layer_enum,
                        content=request.content,
                        source_type=SourceType.EXPLICIT,
                        status=MemoryStatus.ACTIVE,
                        is_latest=True,
                        embedding=embedding,
                        memory_at=request.memory_at,
                        custom=_raw_custom,
                    )
                    memory_id = await vector_store.upsert(mem_node)
                    response.memory_id = memory_id
                    s.set_output({"memory_id": memory_id, "layer": suggested_layer})
                    logger.debug(f"[write] persisted: id={memory_id} layer={suggested_layer}")
                except Exception as persist_err:
                    _l1_error = str(persist_err)
                    s.set_error(_l1_error)
                    logger.error(f"V1 Write: Persist failed (non-fatal): {persist_err}", exc_info=True)
            _l1_ms = (datetime.now() - _t_l1).total_seconds() * 1000
            # 后端是否启用了 sparse 全文索引。
            _sparse_enabled = bool(getattr(vector_store, "supports_fulltext", False))
            await self._emit_pipeline_step(
                request,
                step="S1_L1_UPSERT",
                parsed={
                    "memory_id": memory_id or None,
                    "layer": suggested_layer,
                    "content": (mem_node.content if mem_node else request.content),
                    "tags": list(mem_node.tags) if (mem_node and mem_node.tags) else [],
                    "sparse_enabled": _sparse_enabled,
                    "error": _l1_error,
                },
                elapsed_ms=_l1_ms,
                memory_ids=[memory_id] if memory_id else None,
            )

            # ── Timing: sys1_l1_process_ms ──
            _sys1_l1_process_ms = (datetime.now() - _t_process_start).total_seconds() * 1000
            logger.info(f"TRACE_PERF [{_req_id_perf}] S1_L1_DONE l1_ms={_sys1_l1_process_ms:.0f}ms")

            # 4. MemAgent 处理（可选）
            agent_mode = request.extra.get("agent_mode", "disabled")
            response.extra["agent_mode"] = agent_mode

            _t_workflow = datetime.now()
            if (agent_mode == "full" and vector_store is not None
                    and mem_node is not None and memory_id):
                # ── 获取历史上下文（最近 k 轮对话，供 extractor 使用；k 由 config 配置）──
                _history_context = ""
                _recent_history_ms = 0.0
                _recent_history_count = 0
                try:
                    _hist_turns = getattr(self.config.extractor, "history_turns", 5) or 5
                    _t_recent_history = datetime.now()
                    _history_context = await self._get_recent_history(
                        vector_store, request.user_id, request.agent_id,
                        exclude_memory_id=memory_id,
                        max_turns=_hist_turns * 2,  # k 轮对话 = k*2 条 message
                    )
                    _recent_history_ms = (_t_recent_history and (datetime.now() - _t_recent_history).total_seconds() * 1000) or 0.0
                    _recent_history_count = len([line for line in (_history_context or "").splitlines() if line.strip()])
                except Exception as _hist_err:
                    logger.warning(f"[write] get_recent_history failed: {_hist_err}")

                with tracer.span("mem_agent") as s:
                    await self._run_agent(
                        request=request,
                        response=response,
                        vector_store=vector_store,
                        mem_node=mem_node,
                        memory_id=memory_id,
                        tracer_span=s,
                        history_context=_history_context,
                        recent_history_ms=_recent_history_ms,
                        recent_history_count=_recent_history_count,
                    )
                    total_tokens += response.extra.get("_agent_tokens", 0)
                if response.extra.get("agent_status") == "failed":
                    await self._emit_pipeline_step(
                        request,
                        step="S1_AGENT_FAILED",
                        parsed={
                            "error_code": response.extra.get("agent_error_code", ""),
                            "error": response.extra.get("agent_error", ""),
                        },
                    )
                    await self._rollback_failed_l1(
                        request=request,
                        response=response,
                        vector_store=vector_store,
                        memory_id=memory_id,
                    )
                    response.tokens_used = total_tokens
                    _failed_workflow_ms = (
                        datetime.now() - _t_workflow
                    ).total_seconds() * 1000
                    response.extra["timing"] = {
                        "sys1_waiting_ms": round(_sys1_waiting_ms, 1),
                        "sys1_l1_process_ms": round(_sys1_l1_process_ms, 1),
                        "sys1_workflow_ms": round(_failed_workflow_ms, 1),
                        "sys1_ops_avg_ms": 0.0,
                    }
                    try:
                        MetricsCollector.get().sys1_end(
                            response.extra["timing"], success=False
                        )
                    except Exception:
                        pass
                    return response
                elif (
                    request.extra.get("finalize_session")
                    and (
                        getattr(self.config.recall, "session_episode_enabled", False)
                        or getattr(self.config.recall, "dynamic_episode_enabled", False)
                        or getattr(self.config.recall, "series_episode_enabled", False)
                    )
                ):
                    try:
                        from ..agent.episode_summary import (
                            finalize_dynamic_episode_capsules,
                            finalize_series_episode_containers,
                            finalize_session_episode,
                        )
                        finalize = (
                            finalize_dynamic_episode_capsules
                            if (
                                getattr(self.config.recall, "dynamic_episode_enabled", False)
                                or getattr(self.config.recall, "series_episode_enabled", False)
                            )
                            else finalize_session_episode
                        )
                        finalize_kwargs = dict(
                            vector_store=vector_store,
                            embed_service=self.embed_service,
                            llm_provider=self.llm_provider,
                            user_id=request.user_id,
                            agent_id=request.agent_id,
                            session_id=request.session_id or "default_session",
                        )
                        if finalize is finalize_dynamic_episode_capsules:
                            finalize_kwargs["summary_concurrency"] = getattr(
                                self.config.recall,
                                "dynamic_episode_summary_concurrency", 3,
                            )
                        async with episode_slot(), workload_scope("derived"):
                            response.extra["session_episode"] = await finalize(
                                **finalize_kwargs
                            )
                        if getattr(self.config.recall, "series_episode_enabled", False):
                            try:
                                await self._await_graph_projection(request)
                                response.extra["series_episode"] = (
                                    await finalize_series_episode_containers(
                                        vector_store=vector_store,
                                        graph_store=self._graph_store,
                                        user_id=request.user_id,
                                        agent_id=request.agent_id,
                                    )
                                )
                            except Exception as series_error:
                                logger.warning(
                                    "[series-episode] finalize failed: %s", series_error,
                                )
                                response.extra["series_episode"] = {
                                    "status": "failed", "error": str(series_error),
                                }
                    except Exception as episode_error:
                        logger.warning("[session-episode] finalize failed: %s", episode_error)
                        response.extra["session_episode"] = {
                            "status": "failed", "error": str(episode_error),
                        }
                        if (
                            getattr(self.config.recall, "dynamic_episode_enabled", False)
                            or getattr(self.config.recall, "series_episode_enabled", False)
                        ):
                            # L1/L2 already completed. Replaying the whole final
                            # chunk to repair a derived L3 can repeat reconcile
                            # mutations, so expose a non-retryable partial success.
                            response.success = False
                            response.error_code = 409
                            response.error_message = (
                                "[DYNAMIC_EPISODE_FINALIZE_FAILED] base memories "
                                "were persisted; automatic chunk retry is unsafe: "
                                f"{episode_error}"
                            )
                            response.extra["partial_agent_persist"] = True
                            await self._await_graph_projection(request)
                            return response
            elif agent_mode == "full":
                self._mark_agent_failure(
                    response,
                    error_code="L1_UPSERT_FAILED",
                    error_message=_l1_error or "L1 raw memory was not persisted",
                )
                await self._emit_pipeline_step(
                    request,
                    step="S1_AGENT_SKIPPED",
                    parsed={
                        "reason": "l1_upsert_failed_or_no_vector_store",
                        "error": _l1_error or "",
                    },
                )
                response.tokens_used = total_tokens
                return response

            # Graph starts after L2 persist and overlaps Dynamic Episode, but a
            # synchronous write does not return before its own projection ends.
            await self._await_graph_projection(request)

            # ── Timing: sys1_workflow_ms ──
            _sys1_workflow_ms = (datetime.now() - _t_workflow).total_seconds() * 1000
            logger.info(f"TRACE_PERF [{_req_id_perf}] S1_WORKFLOW_DONE workflow_ms={_sys1_workflow_ms:.0f}ms")

            # 5. 合并检测（可选）
            enable_merge_check = request.extra.get("enable_merge_check", False)
            should_merge = response.extra.get("should_merge", False)

            if enable_merge_check and not should_merge and request.existing_memories:
                with tracer.span("merge_check") as s:
                    existing_for_merge = [
                        {"memory_id": mem.get("memory_id", ""), "content": mem.get("content", "")}
                        for mem in request.existing_memories
                    ]
                    merge_result = self.merger.check_merge(
                        new_content=request.content,
                        existing_memories=existing_for_merge,
                    )
                    response.extra["should_merge"] = merge_result.should_merge
                    response.extra["merge_target_id"] = merge_result.target_memory_id or ""
                    s.set_output({
                        "should_merge": merge_result.should_merge,
                        "target_id": merge_result.target_memory_id or "",
                    })

            response.success = True
            response.tokens_used = total_tokens

            # ── 汇总 sys1 timing ──
            _ops_count = len(response.extra.get("agent_stored_ids", []))
            _ops_avg_ms = _sys1_workflow_ms / _ops_count if _ops_count > 0 else 0
            response.extra["timing"] = {
                "sys1_waiting_ms": round(_sys1_waiting_ms, 1),
                "sys1_l1_process_ms": round(_sys1_l1_process_ms, 1),
                "sys1_workflow_ms": round(_sys1_workflow_ms, 1),
                "sys1_ops_avg_ms": round(_ops_avg_ms, 1),
            }

            # Metrics: S1 完成 + VDB ops
            _mc = MetricsCollector.get()
            _mc.sys1_end(response.extra["timing"], success=True)
            if _ops_count > 0:
                for _ in range(_ops_count):
                    _mc.record_vdb_op(_ops_avg_ms)

        except Exception as e:
            logger.error(f"MemoryWriter.write failed: {e}", exc_info=True)
            response.error_code = 500
            response.error_message = str(e)
            tracer.set_error(str(e))
            # Metrics: S1 失败
            try:
                MetricsCollector.get().sys1_end({}, success=False)
            except Exception:
                pass
        finally:
            await self._await_graph_projection(request)
            response.extra["workload"] = workload_snapshot()
            self._attach_graph_executor_diagnostics(response)
            response.elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            tracer.set_output({
                "success": response.success,
                "layer": response.layer,
                "memory_id": response.memory_id,
                "entities_count": len(response.entities),
                "entities": [
                    {"name": e.get("name", ""), "type": e.get("type", "")}
                    if isinstance(e, dict) else {"name": str(e)}
                    for e in response.entities
                ],
                "summary": response.extra.get("summary", ""),
                "content_stored": request.content,
                "tokens_used": response.tokens_used,
                "pipeline_ms": response.elapsed_ms,
                "agent_status": response.extra.get("agent_status", ""),
            })
            await self._emit_write_timeline(request, tracer)
            tracer.finish(write_file=False)

        return response

    async def _get_recent_history(
        self,
        vector_store: VectorStoreBase,
        user_id: str,
        agent_id: Optional[str] = None,
        exclude_memory_id: str = "",
        max_turns: int = 20,
        max_chars_assistant: int = 500,
    ) -> str:
        """
        获取该用户最近的 L1_RAW 对话记录，按轮次拆分后拼成 history context。

        每条 L1_RAW 的 content 是多轮对话（[user]: ...\n[assistant]: ...）。
        拆成单条 message 后，user 消息完整保留，assistant 消息截取前 max_chars_assistant 字符。
        最终取最近 max_turns 条 message（注意单位是 message 条数，非对话轮数；
        调用方按 k 轮 × 2 传入）。

        Returns:
            格式化的历史上下文字符串，空字符串表示无历史。
        """
        try:
            # 仅需 content/node_id/gmt_created 拼历史，不涉及 embedding。
            # allow_raw_layer=True：向量库默认对所有查询排除 L1_RAW（对外读路径
            # 不返回原始对话），而本处正是"内部管线读原始对话"的唯一场景，
            # 不放行会拿不到历史、直接降低提取质量。
            nodes = await vector_store.list_by_user(
                user_id=user_id,
                agent_id=agent_id,
                layers=[MemoryLayer.L1_RAW],
                status_filter=[MemoryStatus.ACTIVE, MemoryStatus.SHADOW],
                limit=max(50, max_turns),
                with_vectors=False,
                allow_raw_layer=True,
            )
            if not nodes:
                return ""

            # 排除当前正在处理的 memory（避免自引用）
            nodes = [n for n in nodes if n.node_id != exclude_memory_id]

            # 按 gmt_created 升序（时间正序）
            nodes.sort(key=lambda n: n.gmt_created or datetime.min)

            # 拆分每条 L1_RAW 内容为单条 messages。
            # L1_RAW content 格式: "[user]: xxx\n[assistant]: yyy\n[user]: zzz..."
            # 多真人对话还会在**文首**多一行 "用户名叫Tim"（见 WriteRequest.get_flat_content）。
            # 关键：单条 message 的 content 本身可能含换行（多行 user 输入），
            # 因此必须按「[label]: 前缀」识别消息边界，无前缀的行视为上一条消息的续行
            # 归属其下，而不是逐行当成独立 message——否则续行会被当成裸消息，且后续
            # 按行数截断时可能从一条多行消息中间切开，导致前缀行丢失、续行裸露在开头。
            #
            # label 除了 4 个已知 role，还可能是对方真人的名字（[John]:）。名字长度设上限
            # 避免把正文里形如 "[注]: xxx" 的普通文本误判成消息边界。
            import re as _re
            from .base import parse_user_name_line
            _KNOWN_ROLES = {"user", "assistant", "system", "tool"}
            # 带方括号的任意 label（含对方真人名字）
            _labeled_prefix = _re.compile(r"^\[([^\]\n]{1,32})\]:\s?")
            # 无方括号的已知 role（历史数据容错，沿用旧正则的 `\[?...\]?` 宽容度）
            _bare_role_prefix = _re.compile(r"^(user|assistant|system|tool):\s?")

            # (label, content) 元组列表；label 为 role 或对方真人名字
            parsed: List[Tuple[str, str]] = []
            user_name: str = ""
            for node in nodes:
                content = node.content or ""
                for _line_idx, line in enumerate(content.split("\n")):
                    m = _labeled_prefix.match(line) or _bare_role_prefix.match(line)
                    if m:
                        label = m.group(1)
                        body = line[m.end():]
                        parsed.append((label, body))
                        continue
                    # 名字行只可能是每个 node content 的第一行（get_flat_content 插在文首）。
                    # 逐 node 判断而非用 `not parsed`：parsed 跨 node 累积，第二个 node
                    # 起就非空了，那样名字行会被误当成上一条消息的续行拼进去。
                    # 剔除后在文首统一输出一次，避免占 max_turns 配额并重复刷屏。
                    if _line_idx == 0:
                        _name = parse_user_name_line(line)
                        if _name:
                            user_name = _name
                            continue
                    if parsed:
                        # 续行：拼回上一条 message（保留换行）
                        prev_label, prev_body = parsed[-1]
                        parsed[-1] = (prev_label, prev_body + "\n" + line)
                    else:
                        # 开头就是无前缀行（异常/老数据）：当成 user 消息兜底
                        parsed.append(("user", line))

            # 取最近 max_turns 条 message（按 message 截断，绝不从消息中间切开）
            recent = parsed[-max_turns:] if len(parsed) > max_turns else parsed
            if not recent:
                return ""

            # 格式化：非 user 侧整条截断（含对方真人），user 完整保留
            all_messages: List[str] = []
            if user_name:
                from .base import render_user_name_line
                from ..utils.lang_detect import is_chinese

                _name_line = render_user_name_line(
                    user_name,
                    is_chinese("\n".join(b for _, b in recent)),
                )
                if _name_line:
                    all_messages.append(_name_line)
            for label, body in recent:
                body = body.strip()
                # 截断口径：assistant + 对方真人（非已知 role 的 label）。
                # user 完整保留；system / tool 沿用历史行为（不截断）。
                _truncate = label == "assistant" or label not in _KNOWN_ROLES
                if _truncate and len(body) > max_chars_assistant:
                    body = body[:max_chars_assistant] + "..."
                all_messages.append(f"[{label}]: {body}")

            return "\n".join(all_messages)
        except Exception as e:
            logger.debug(f"[write] _get_recent_history error: {e}")
            return ""

    async def _rollback_failed_l1(
        self,
        *,
        request: WriteRequest,
        response: WriteResponse,
        vector_store: Optional[VectorStoreBase],
        memory_id: str,
    ) -> None:
        """Delete this request's provisional L1_RAW after full-agent failure.

        A retryable write must not leave the same raw chunk in history. L2 changes
        are deliberately not deleted here: reconcile may have updated existing
        nodes, so treating all returned ids as newly-created would corrupt prior
        memory. The next retry/reconcile converges those partial high-level writes.
        """
        deleted = False
        rollback_error = ""
        if memory_id and vector_store is not None:
            try:
                delete_accepted = bool(await vector_store.delete(memory_id))
                if not delete_accepted:
                    rollback_error = "vector store did not confirm deletion"
                else:
                    remaining = await vector_store.get_by_id(memory_id)
                    deleted = remaining is None
                    if not deleted:
                        rollback_error = "raw memory still exists after deletion"
            except Exception as exc:
                rollback_error = f"{type(exc).__name__}: {exc}"
        elif memory_id:
            rollback_error = "vector store unavailable"

        response.extra["l1_rollback"] = {
            "memory_id": memory_id,
            "deleted": deleted,
            "error": rollback_error,
            "retry_safe": deleted,
        }
        if deleted:
            response.memory_id = ""
        partial_agent_persist = bool(response.extra.get("agent_stored_ids"))
        if partial_agent_persist:
            response.extra["partial_agent_persist"] = True
            response.extra["l1_rollback"]["retry_safe"] = False
        if rollback_error or partial_agent_persist:
            unsafe_reason = (
                f"L1 rollback failed for memory_id={memory_id}: {rollback_error}"
                if rollback_error
                else "agent partially persisted L2 changes"
            )
            if rollback_error:
                response.extra["l1_rollback_failed"] = True
            # Retrying after an unconfirmed L1 rollback or partial L2 mutation can
            # duplicate facts or reapply UPDATE/SUPERSEDE operations. Surface a
            # non-retryable conflict so recovery can inspect and reconcile first.
            response.error_code = 409
            response.error_message = (
                f"{response.error_message}; {unsafe_reason}; automatic retry is unsafe"
            ).strip("; ")
        await self._emit_pipeline_step(
            request,
            step="S1_L1_ROLLBACK",
            parsed=response.extra["l1_rollback"],
            memory_ids=[memory_id] if memory_id else None,
        )

    async def _collect_existing_tags(
        self,
        vector_store: VectorStoreBase,
        user_id: str,
        agent_id: Optional[str] = None,
    ) -> List[str]:
        """
        收集该用户在 VDB 中已有的所有 tags（去重）。

        从 vector_store.list_by_user 的结果中提取所有 tags 字段，
        合并为唯一集合，供 extract prompt 引导 LLM 优先复用已有标签。
        失败静默返回空列表（不阻塞主流程）。
        """
        key = (user_id or "", agent_id or "")
        try:
            ttl = max(0.0, float(os.getenv("MEMORY_EXISTING_TAGS_CACHE_TTL_SEC", "1800")))
        except (TypeError, ValueError):
            ttl = 1800.0
        now = time.monotonic()
        cache = getattr(self, "_existing_tags_cache", None)
        if cache is None:
            cache = self._existing_tags_cache = {}
        cached = cache.get(key)
        if ttl > 0 and cached is not None and cached[0] >= now:
            return list(cached[1])

        locks = getattr(self, "_existing_tags_locks", None)
        if locks is None:
            locks = self._existing_tags_locks = {}
        lock = locks.get(key)
        if lock is None:
            lock = locks[key] = asyncio.Lock()

        async with lock:
            now = time.monotonic()
            cached = cache.get(key)
            if ttl > 0 and cached is not None and cached[0] >= now:
                return list(cached[1])
            try:
                nodes = await vector_store.list_by_user(
                    user_id=user_id,
                    agent_id=agent_id,
                    limit=10000,
                    with_vectors=False,
                )
                tag_set: set = set()
                for node in nodes:
                    if hasattr(node, "tags") and node.tags:
                        tag_set.update(node.tags)
                tags = tuple(sorted(tag_set))
                if ttl > 0:
                    cache[key] = (now + ttl, tags)
                if tags:
                    logger.debug(
                        f"[write] collected {len(tags)} existing tags for user={user_id}"
                    )
                return list(tags)
            except Exception as e:
                logger.warning(f"[write] _collect_existing_tags failed (non-fatal): {e}")
                return []

    def _remember_existing_tags(
        self,
        user_id: str,
        agent_id: Optional[str],
        tags,
    ) -> None:
        """Increment an already-warmed prompt tag cache after a successful write."""
        new_tags = {str(tag) for tag in (tags or []) if str(tag).strip()}
        if not new_tags:
            return
        cache = getattr(self, "_existing_tags_cache", None)
        if not cache:
            return
        key = (user_id or "", agent_id or "")
        cached = cache.get(key)
        if cached is None:
            return
        expires_at, old_tags = cached
        cache[key] = (expires_at, tuple(sorted(set(old_tags).union(new_tags))))

    async def _run_agent(
        self,
        request: WriteRequest,
        response: WriteResponse,
        vector_store: VectorStoreBase,
        mem_node: MemoryNode,
        memory_id: str,
        tracer_span,
        history_context: str = "",
        recent_history_ms: float = 0.0,
        recent_history_count: int = 0,
    ) -> None:
        """
        MemAgent 完整处理流程：extract → reconcile & store → summary。
        结果写回 response.extra。
        """
        mode = ProcessMode.FULL
        existing_memories = []
        for mem in (request.existing_memories or []):
            existing_memories.append({
                "memory_id": mem.get("memory_id", ""),
                "content": mem.get("content", ""),
                "layer": mem.get("layer", ""),
                "embedding": mem.get("embedding", []),
            })

        extract_timing = {
            "recent_history_ms": round(recent_history_ms, 2),
            "recent_history_count": recent_history_count,
            "collect_existing_tags_ms": 0.0,
            "existing_tags_count": 0,
            "extract_total_ms": 0.0,
        }

        try:
            # 收集该用户已有的所有 tags（供 extractor prompt 引导复用）
            _t_existing_tags = datetime.now()
            existing_tags = await self._collect_existing_tags(
                vector_store, request.user_id, request.agent_id,
            )
            extract_timing["collect_existing_tags_ms"] = round(
                (datetime.now() - _t_existing_tags).total_seconds() * 1000, 2
            )
            extract_timing["existing_tags_count"] = len(existing_tags or [])

            # 构建 extractor 输入内容：优先用原始 messages dump
            # 带 name 时必须透传：多真人对话靠它区分「对方是另一个真人」还是 AI assistant
            # （role=assistant + name → 真人），也是 basic_info.name 的权威来源。
            if request.messages:
                import json as _json_msgs
                _msgs_dump = []
                for m in request.messages:
                    _item = {"role": m.role, "content": m.content}
                    if getattr(m, "name", None):
                        _item["name"] = m.name
                    _msgs_dump.append(_item)
                _extract_content = _json_msgs.dumps(_msgs_dump, ensure_ascii=False)
            else:
                _extract_content = request.content

            # 基础画像 schema 字段（{name: description}），由 extractor 渲染到 prompt
            _basic_profile_fields = self.config.basic_profile.effective_fields()

            # 本次有效 summary 开关（per-call > config）。summary 不再由 mem_agent 单次生成，
            # 改由下方 rolling buffer 流程处理（仅 messages），因此这里给 mem_agent 传 False，
            # 让老的「单次 add 即 summary」路径自然成为 no-op（零风险）。

            _t_extract_total = datetime.now()
            agent_result = await self.mem_agent.process_add(
                content=_extract_content,
                context={"uid": request.user_id, "agent_id": request.agent_id},
                mode=mode,
                existing_memories=existing_memories,
                memory_at=request.memory_at,
                existing_tags=existing_tags,
                history_context=history_context,
                basic_profile_fields=_basic_profile_fields,
            )
            extract_timing["extract_total_ms"] = round(
                (datetime.now() - _t_extract_total).total_seconds() * 1000, 2
            )
        except Exception as agent_err:
            logger.error(f"[write] MemAgent process_add raised: {agent_err}", exc_info=True)
            self._mark_agent_failure(
                response,
                error_code="AGENT_EXCEPTION",
                error_message=str(agent_err),
            )
            response.extra["extract_timing"] = extract_timing
            tracer_span.set_output({"success": False, "error": str(agent_err)})
            return

        if agent_result is None or not agent_result.success:
            await self._handle_agent_failure(request, response, agent_result, tracer_span, extract_timing=extract_timing)
            return

        # ── 提取成功 ──
        _perf_req_id = request.request_id or get_request_id()
        logger.info(
            f"TRACE_PERF [{_perf_req_id}] S1_EXTRACT_DONE "
            f"extract_ms={agent_result.extract_elapsed_ms:.0f} "
            f"summary_ms={agent_result.summary_elapsed_ms:.0f} "
            f"memory={len((agent_result.extracted_info or {}).get('memory', (agent_result.extracted_info or {}).get('facts', [])))}"
        )

        # ── basic_info: prompt-driven schema, no LLM function-calling ──
        # extractor 把 basic_info dict 放进 extracted_info；writer 在此 upsert L0_BASIC_INFO
        # 演化链。失败/无效都不抛错，落 tool_results_summary 走原 TOOL_CALLS pipeline log。
        basic_info_raw = None
        if isinstance(agent_result.extracted_info, dict):
            basic_info_raw = agent_result.extracted_info.pop("basic_info", None)

        tool_calls_raw = None  # v0.1.5.13+ 不再有真实 LLM tool_calls；保留字段做兼容
        tool_results_summary: List[Dict[str, Any]] = []

        if isinstance(basic_info_raw, dict) and basic_info_raw:
            try:
                _bp_result = await upsert_basic_profile(
                    user_id=request.user_id,
                    kv=basic_info_raw,
                    vector_store=vector_store,
                    embed_service=self.embed_service,
                    allowed_fields=list(_basic_profile_fields.keys()),
                    client_meta=getattr(request, "client_meta", None),
                )
                # 把 upsert 结果包成原 tool_results 形态（向后兼容 pipeline log 解析）
                tool_results_summary.append({
                    "tool": "basic_profile_upsert",  # 不再是 LLM tool name
                    "round": 1,
                    "success": _bp_result.success,
                    "data": _bp_result.to_dict(),
                    "error": _bp_result.error,
                })
                # input 侧记录 LLM 给出的 basic_info 原始值（trace 可追溯）
                tool_calls_raw = [{
                    "function": {
                        "name": "basic_profile_upsert",
                        "arguments": json.dumps(basic_info_raw, ensure_ascii=False, default=str),
                    }
                }]
            except Exception as bp_err:
                logger.error(f"[write] upsert_basic_profile raised: {bp_err}", exc_info=True)
                tool_results_summary.append({
                    "tool": "basic_profile_upsert",
                    "round": 1,
                    "success": False,
                    "error": str(bp_err),
                })

        extract_timing["extract_llm_ms"] = round(getattr(agent_result, "extract_elapsed_ms", 0) or 0, 2)
        response.extra["extract_timing"] = extract_timing
        response.extra["tool_results"] = tool_results_summary

        # 写 TOOL_CALLS pipeline log（可观测 tool 调用链路）
        if self._cache and (tool_calls_raw or tool_results_summary):
            try:
                import json as _json_tc
                _tc_req_id = request.request_id or get_request_id()
                tc_data = {
                    "tool_calls_input": (
                        [{"name": tc.get("function", {}).get("name", ""), "arguments": tc.get("function", {}).get("arguments", "")}
                         for tc in tool_calls_raw]
                        if isinstance(tool_calls_raw, list) else str(tool_calls_raw)[:500]
                    ) if tool_calls_raw else [],
                    "tool_results": tool_results_summary,
                    "tool_calls_only": bool(
                        tool_calls_raw
                        and not (agent_result.extracted_info or {}).get("memory")
                        and not (agent_result.extracted_info or {}).get("facts")
                        and not (agent_result.extracted_info or {}).get("identity")
                    ),
                }
                await self._cache.store_pipeline_log(
                    request_id=_tc_req_id,
                    user_id=request.user_id,
                    agent_id=request.agent_id or "default_agent",
                    session_id=request.session_id or "",
                    step="TOOL_CALLS",
                    prompt="",
                    response=_json_tc.dumps(tc_data, ensure_ascii=False, default=str),
                    parsed=_json_tc.dumps(tc_data, ensure_ascii=False, default=str),
                    memory_ids=[],
                )
            except Exception as e:
                logger.warning(f"[write] store TOOL_CALLS log failed: {e}")
        # 过滤实体
        _CATEGORY_BLACKLIST = {
            "locations", "location", "persons", "person",
            "organizations", "organization", "events", "event",
            "relations", "relation", "products", "product",
            "animals", "animal", "foods", "food",
            "technologies", "technology", "tech",
            "attributes", "attribute", "others", "other",
        }
        if agent_result.extracted_info and isinstance(agent_result.extracted_info, dict):
            for entity in (agent_result.extracted_info.get("entities") or []):
                if isinstance(entity, dict):
                    name = entity.get("name", "")
                    if name and name.lower() not in _CATEGORY_BLACKLIST:
                        response.entities.append(entity)
                elif isinstance(entity, str):
                    if entity.lower() not in _CATEGORY_BLACKLIST:
                        response.entities.append({"name": entity})

        response.extra["summary"] = agent_result.summary or ""
        if agent_result.suggested_layer:
            response.layer = agent_result.suggested_layer

        conflicts = []
        for conflict in (agent_result.conflicts or []):
            conflicts.append({
                "type": conflict.get("type", ""),
                "target_id": conflict.get("target_id", ""),
                "description": conflict.get("description", ""),
                "resolution": conflict.get("resolution", ""),
            })
        response.extra["conflicts"] = conflicts
        response.extra["should_merge"] = agent_result.should_merge
        response.extra["merge_target_id"] = agent_result.merge_target_id or ""
        response.extra["_agent_tokens"] = agent_result.tokens_used
        # extract 链路 LLM token 消耗（prompt/completion/total），供 client 透传给调用方
        response.extra["extract_tokens"] = {
            "prompt": getattr(agent_result, "extract_prompt_tokens", 0) or 0,
            "completion": getattr(agent_result, "extract_completion_tokens", 0) or 0,
            "total": getattr(agent_result, "extract_tokens_used", 0) or 0,
        }

        # 写 pipeline logs
        _req_id = request.request_id or get_request_id()
        await self._write_extract_log(
            request,
            agent_result,
            _req_id,
            tool_results=tool_results_summary,
            extract_timing=extract_timing,
        )
        await self._write_summary_log(request, agent_result, _req_id)

        # Reconcile & store
        stored_ids: List[str] = []
        _vs_logger = logging.getLogger("hy_memory.data.vector_store_chroma")
        _vs_level = _vs_logger.level
        _vs_logger.setLevel(logging.INFO)
        try:
            new_memory_texts, new_memories_meta = self._collect_new_memories(
                agent_result.extracted_info or {}
            )

            # extractor 结果去重：新提取的多条之间互相判重；向量继续透传给
            # reconcile / store。还没入库，故 delete_from_store=False。
            precomputed_embeddings: Dict[str, List[float]] = {}
            if len(new_memory_texts) >= 2:
                try:
                    (
                        new_memory_texts,
                        new_memories_meta,
                        precomputed_embeddings,
                    ) = await self._dedup_extracted(
                        new_memory_texts, new_memories_meta, request, _req_id,
                    )
                except Exception as e:
                    logger.warning(f"[write] extractor dedup failed (non-fatal): {e}")

            if new_memory_texts:
                if _RECONCILE_ENABLED:
                    stored_ids, recon_error, ops_counts, recon_tokens = await self._reconcile_and_store(
                        new_memory_texts=new_memory_texts,
                        new_memories_meta=new_memories_meta,
                        request=request,
                        vector_store=vector_store,
                        req_id=_req_id,
                        source_raw_memory_id=memory_id,
                        precomputed_embeddings=precomputed_embeddings,
                    )
                else:
                    stored_ids, recon_error, ops_counts, recon_tokens = await self._direct_store(
                        new_memories_meta=new_memories_meta,
                        request=request,
                        vector_store=vector_store,
                        req_id=_req_id,
                        source_raw_memory_id=memory_id,
                        precomputed_embeddings=precomputed_embeddings,
                    )
                response.extra["reconcile_ops"] = ops_counts
                response.extra["reconcile_tokens"] = recon_tokens
                if recon_error is not None:
                    response.extra["agent_stored_ids"] = stored_ids
                    self._mark_agent_failure(
                        response,
                        error_code="RECONCILE_FAILED",
                        error_message=recon_error,
                        agent_nodes=len(stored_ids),
                    )
                    logger.warning(f"[write] reconcile failed: {recon_error}")
                    return

            # Intentions → L7（直接 upsert，不走 reconcile）
            # MEMORY_ENABLE_INTENTION / LLMConfig.enable_intention 关闭时整段跳过
            if getattr(self.config.llm, "enable_intention", True):
                intentions = self._collect_intentions(agent_result.extracted_info or {})
                if intentions:
                    intention_ids = await self._store_intentions(
                        intentions=intentions,
                        request=request,
                        vector_store=vector_store,
                        req_id=_req_id,
                    )
                    stored_ids.extend(intention_ids)
                    logger.info(
                        f"TRACE_PERF [{_perf_req_id}] S1_INTENTION_DONE "
                        f"intentions={len(intention_ids)}"
                    )
            else:
                logger.debug(
                    "[write] enable_intention=false, skip L7 intention store"
                )

            response.extra["agent_stored_ids"] = stored_ids
            response.extra["agent_status"] = "success"
            response.extra["agent_error"] = ""
            response.extra["agent_nodes"] = len(stored_ids)
            _vs_logger.setLevel(_vs_level)
            logger.info(
                f"TRACE_PERF [{_perf_req_id}] S1_PERSIST_DONE "
                f"ops={len(stored_ids)} nodes to vector store"
            )
            logger.debug(f"[agent] persisted {len(stored_ids)} nodes to vector store")

            # L1_RAW → SHADOW
            #
            # 条件是「agent 跑完且这个节点确实是 L1_RAW」，而**不看提取出几条**：
            # extractor 判定「这段对话没有可提取的用户事实」（如纯技术问答）时
            # 返回 memory=[]，这是**正常结论**而非失败，原始对话同样该退居备份层。
            # 此前条件带 stored_ids，空提取时 stored_ids 为空 → 不降级且零埋点，
            # L1_RAW 会长期停留在 ACTIVE 被 list/search/keyword_search 召回。
            #
            # 提取失败不会走到这里：agent 抛异常、agent_result.success=False、
            # reconcile 失败三条路径都在上方提前 return，故失败场景仍保留
            # ACTIVE（数据可能还有救，留档待重试）。
            #
            # layer 守卫不可省：调用方可通过 metadata 传 extra["layer"] 显式指定
            # 层级（见本文件 Layer 分配段），此时 mem_node 并非原始对话节点，
            # 降级它等于逻辑删除调用方刚写入的数据。
            if memory_id and mem_node.layer == MemoryLayer.L1_RAW:
                try:
                    mem_node.status = MemoryStatus.SHADOW
                    await vector_store.upsert(mem_node)
                    logger.debug(f"[agent] L1 raw {memory_id} status → SHADOW")
                except Exception as shadow_err:
                    logger.warning(f"[agent] failed to shadow L1 raw: {shadow_err}")

        except Exception as persist_err:
            _vs_logger.setLevel(_vs_level)
            logger.error(f"[agent] persist failed: {persist_err}", exc_info=True)
            response.extra["agent_stored_ids"] = stored_ids
            self._mark_agent_failure(
                response,
                error_code="AGENT_PERSIST_FAILED",
                error_message=f"agent persist failed: {persist_err}",
                agent_nodes=len(stored_ids),
            )
            return

        tracer_span.set_output({
            "success": True,
            "entities_count": len(response.entities),
            "summary": agent_result.summary or "",
            "conflicts_count": len(conflicts),
            "tokens_used": agent_result.tokens_used,
            "should_merge": agent_result.should_merge,
            "agent_stored_count": len(stored_ids),
        })

    def _extract_llm_diag(self, agent_result) -> Dict[str, Any]:
        """提取 EXTRACT LLM 响应诊断字段，用于排查 EMPTY_RESPONSE / 长耗时空返回。"""
        _er = getattr(agent_result, "_extract_result", None) if agent_result is not None else None
        if _er is None:
            return {}
        return {
            "llm_finish_reason": getattr(_er, "llm_finish_reason", "") or "",
            "llm_model": getattr(_er, "llm_model", "") or "",
            "llm_response_id": getattr(_er, "llm_response_id", "") or "",
            "llm_choices_count": getattr(_er, "llm_choices_count", 0) or 0,
            "llm_content_len": getattr(_er, "llm_content_len", 0) or 0,
            "llm_tool_calls_count": getattr(_er, "llm_tool_calls_count", 0) or 0,
            "llm_prompt_tokens": getattr(_er, "prompt_tokens", 0) or 0,
            "llm_completion_tokens": getattr(_er, "completion_tokens", 0) or 0,
            "llm_total_tokens": getattr(_er, "tokens_used", 0) or 0,
        }

    @staticmethod
    def _mark_agent_failure(
        response: WriteResponse,
        *,
        error_code: str,
        error_message: str,
        agent_nodes: int = 0,
    ) -> None:
        """Make an internal agent failure visible to every write caller.

        L1_RAW may already have been persisted, but a full-mode write has not
        completed its memory contract when extraction/reconcile/persist fails.
        The caller must see ``success=False`` so checkpointed ingestion can retry.
        """
        code = error_code or "AGENT_FAILED"
        message = error_message or "agent processing failed"
        response.extra["agent_status"] = "failed"
        response.extra["agent_error"] = message
        response.extra["agent_error_code"] = code
        response.extra["agent_nodes"] = int(agent_nodes or 0)
        response.success = False
        response.error_code = 502
        response.error_message = f"[{code}] {message}"

    async def _handle_agent_failure(self, request, response, agent_result, tracer_span, extract_timing: Optional[Dict[str, Any]] = None) -> None:
        """处理 agent extract 失败的情况。"""
        if agent_result is None:
            self._mark_agent_failure(
                response,
                error_code="AGENT_NO_RESULT",
                error_message="agent processing returned no result",
            )
            tracer_span.set_output({"success": False, "error": "agent processing failed"})
            return

        _error_code = getattr(agent_result, "error_code", "") or "AGENT_FAILED"
        _error_msg = agent_result.error or "agent processing failed"
        self._mark_agent_failure(
            response,
            error_code=_error_code,
            error_message=_error_msg,
        )
        logger.warning(f"[write] agent extract failed: code={_error_code} error={_error_msg}")

        _raw_resp = getattr(agent_result, "extract_raw_response", "") or ""
        # 复原真实 prompt（[SYSTEM]/[USER]），与成功路径 _write_extract_log 一致，
        # 使失败（EMPTY_RESPONSE / JSON_PARSE_FAILED / LLM_ERROR）的 EXTRACT log 也能看到实际输入。
        _extract_prompt = ""
        _attempts = []
        _er = getattr(agent_result, "_extract_result", None)
        if _er is not None:
            if getattr(_er, "_actual_prompt", None):
                _sys = getattr(_er, "_actual_system_prompt", "") or ""
                _usr = getattr(_er, "_actual_prompt", "") or ""
                _extract_prompt = f"[SYSTEM]\n{_sys}\n\n[USER]\n{_usr}"
            _attempts = list(getattr(_er, "attempts", []) or [])
        _parsed = {"error": _error_code, "message": _error_msg}
        if extract_timing:
            _parsed.update(extract_timing)
        _parsed.update(self._extract_llm_diag(agent_result))
        _parsed["extract_llm_ms"] = round(getattr(agent_result, "extract_elapsed_ms", 0) or 0, 2)
        if _attempts:
            # 每一次 LLM 重试的失败明细（类型/消息/HTTP 状态码等），供排障
            _parsed["attempts"] = _attempts
        await self._emit_pipeline_step(
            request,
            step="EXTRACT",
            parsed=_parsed,
            prompt=_extract_prompt,
            response=_raw_resp,
            elapsed_ms=getattr(agent_result, "extract_elapsed_ms", 0) or 0,
            prompt_tokens=getattr(agent_result, "extract_prompt_tokens", 0) or 0,
            completion_tokens=getattr(agent_result, "extract_completion_tokens", 0) or 0,
            total_tokens=getattr(agent_result, "extract_tokens_used", 0) or 0,
        )

        tracer_span.set_output({"success": False, "error": _error_msg})

    async def _write_extract_log(
        self,
        request,
        agent_result,
        req_id: str,
        tool_results: List[Dict[str, Any]] = None,
        extract_timing: Optional[Dict[str, Any]] = None,
    ) -> None:
        """写 EXTRACT pipeline log。"""
        if not self._cache:
            return
        try:
            import json as _json

            # 使用 extractor 返回的真实 prompt（如果有的话）
            _extract_prompt = ""
            _extract_result = getattr(agent_result, '_extract_result', None)
            if _extract_result and hasattr(_extract_result, '_actual_prompt') and _extract_result._actual_prompt:
                _sys = _extract_result._actual_system_prompt or ""
                _usr = _extract_result._actual_prompt or ""
                _extract_prompt = f"[SYSTEM]\n{_sys}\n\n[USER]\n{_usr}"
            else:
                # fallback: 用旧方式构造近似 prompt（不完全准确）
                from ..agent.extractor import EXTRACT_PROMPT
                _current_time = request.memory_at.isoformat(timespec="seconds") if request.memory_at else ""
                _extract_prompt = EXTRACT_PROMPT.format(content=request.content, current_date="", memory_at=_current_time or "", existing_tags="(see actual prompt)", last_messages="")

            # response 字段：优先用 raw_response（LLM 原始输出）
            _raw_resp = agent_result.extract_raw_response or ""

            # parsed 字段：包含 extracted_info + tool_results 元信息
            _parsed_data = dict(agent_result.extracted_info) if agent_result.extracted_info else {}
            if extract_timing:
                _parsed_data.update(extract_timing)
            _parsed_data.update(self._extract_llm_diag(agent_result))
            _parsed_data["extract_llm_ms"] = round(getattr(agent_result, "extract_elapsed_ms", 0) or 0, 2)
            if tool_results:
                _parsed_data["_tool_results"] = tool_results

            await self._cache.store_pipeline_log(
                request_id=req_id,
                user_id=request.user_id,
                agent_id=request.agent_id or "default_agent",
                session_id=request.session_id or "",
                step="EXTRACT",
                prompt=_extract_prompt,
                response=_raw_resp if _raw_resp else _json.dumps(_parsed_data, ensure_ascii=False, default=str),
                parsed=_json.dumps(_parsed_data, ensure_ascii=False, default=str) if _parsed_data else "",
                elapsed_ms=agent_result.extract_elapsed_ms,
                prompt_tokens=agent_result.extract_prompt_tokens,
                completion_tokens=agent_result.extract_completion_tokens,
                total_tokens=agent_result.extract_tokens_used,
            )
        except Exception as e:
            logger.warning(f"[write] store EXTRACT log failed: {e}")

    async def _write_summary_log(self, request, agent_result, req_id: str) -> None:
        """写 SUMMARY pipeline log（若有摘要）。"""
        if not self._cache or not agent_result.summary or not agent_result.summary_tokens_used:
            return
        try:
            import json as _json_s

            # 使用 summarizer 返回的真实 prompt
            _summary_result = getattr(agent_result, '_summary_result', None)
            if _summary_result and hasattr(_summary_result, '_actual_prompt') and _summary_result._actual_prompt:
                _summary_prompt = _summary_result._actual_prompt
            else:
                # fallback
                from ..agent.summarizer import SUMMARY_PROMPT
                from datetime import date as _date_now
                _current_time = request.memory_at.isoformat(timespec="seconds") if request.memory_at else ""
                _memory_date = _current_time[:10] if _current_time and len(_current_time) >= 10 else _date_now.today().isoformat()
                _current_date = _date_now.today().isoformat()
                _summary_prompt = SUMMARY_PROMPT.format(
                    content=request.content or "",
                    memory_date=_memory_date,
                    current_date=_current_date,
                )
            await self._cache.store_pipeline_log(
                request_id=req_id,
                user_id=request.user_id,
                agent_id=request.agent_id or "default_agent",
                session_id=request.session_id or "",
                step="SUMMARY",
                prompt=_summary_prompt,
                response=agent_result.summary,
                parsed=_json_s.dumps({"summary": agent_result.summary}, ensure_ascii=False),
                elapsed_ms=agent_result.summary_elapsed_ms,
                prompt_tokens=agent_result.summary_prompt_tokens,
                completion_tokens=agent_result.summary_completion_tokens,
                total_tokens=agent_result.summary_tokens_used,
            )
        except Exception as e:
            logger.warning(f"[write] store SUMMARY log failed: {e}")

    async def close(self) -> None:
        provider = (
            self._mem_agent.llm_provider if self._mem_agent is not None else None
        )
        if provider is not None and hasattr(provider, "close"):
            await provider.close()
        logger.debug("MemoryWriter closed")
