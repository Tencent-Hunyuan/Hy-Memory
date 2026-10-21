"""
Shared Read Pipeline — retrieval strategy + common post-processing.

Multi-signal fusion architecture:
  - Channel 1: VDB Semantic Search (vector cosine similarity)
  - Channel 2: VDB Keyword Search (Qdrant text index, independent retrieval)
  - Channel 3: Graph Semantic Search (L6_SCHEMA + L7_INTENTION)

Scoring:
  - VDB nodes (additive): final = sem
        + 𝟙[bm25>0] · w_bm25 · bm25_norm
        + 𝟙[ent>0] · w_tag · max(tag_cos, graph_entity_hit)
    Defaults: w_bm25=0.2, w_tag=0.3, tag_cos thr=0.5 (env 可调)
    Tag boost：LLM tag 上的实体；Graph Entity：正文 NER MENTIONS（均排除 PERSON）
  - Graph nodes: raw semantic for display; evidence_boost for intra-pool ranking
  - Graph quota guarantees schema/intention representation in results

Concrete readers differ only in candidate recall:
  - legacy: vector semantic recall
  - hybrid: vector semantic recall + independent BM25 keyword recall

Everything after recall is shared: graph/schema/episode expansion, tag/entity
signals, local rerank, candidate guards, quota merge, evolution, and formatting.
"""

from typing import Any, Dict, List, Optional, Set, Tuple
from datetime import datetime
import asyncio
import json
import os
import logging

from .base import ReadPipeline, ReadRequest, ReadResponse, PipelineContext
from ..config import MemoryConfig
from ..core.embed_service import EmbedService
from ..models.memory import MemoryNode, MemoryLayer, MemoryStatus
from ..data.vector_store import create_vector_store
from ..data.vector_store_base import VectorStoreBase
from ..data.graph_store_base import GraphStoreBase
from ..utils.tracer import PipelineTracer, create_tracer
from ..utils.async_executor import to_thread_guarded
from ._retrieval.lemmatize import lemmatize_for_bm25, get_bm25_params
from ._retrieval.query_expand import expand_query_for_retrieval
from ._retrieval.scoring import (
    normalize_bm25,
    compute_evidence_boost,
    score_vdb_node_additive,
)
from ._retrieval.ner import (
    extract_entities,
    entity_tag_pairs,
    max_entity_tag_cos,
    apply_geo_intent_prior,
    query_day_hints,
    query_time_hints,
    memory_matches_day_hints,
    memory_matches_time_hints,
)
from ._retrieval.diversity import (
    near_dup_demote,
    mmr_select,
    social_boilerplate_demote,
    pool_df_demote,
    blend_rrf_into_scores,
)
from ._retrieval.rrf import rrf_fuse
from ._retrieval.profile_evidence import reverse_lookup_l6
from ._retrieval.evolution import expand_evolution_chains
from ._retrieval.temporal import filter_hits_by_created_after
from ._retrieval.intention import recall_intentions
from dataclasses import replace
from ._retrieval.underflow import fill_normal_slots, normal_count
from ._retrieval.session_episode import (
    is_episode_container,
    merge_episode_members_into_pool,
    rank_dynamic_episode_hits,
    rank_session_episode_hits,
    parent_episode_ids,
    select_episode_capsule_context,
    select_episode_fact_context,
)
from ._retrieval.memory_format import build_rerank_text, format_final_content
from ._retrieval.local_rerank import (
    get_local_reranker,
    rerank_items,
    rerank_items_with_scores,
)
from ._retrieval.hierarchy_bundle import (
    build_hierarchy_bundle_candidate,
    compete_hierarchy_bundle,
)
from ._retrieval.multi_seed_bundle import (
    build_multi_seed_bundle_pool,
    canonical_seed_view,
)
from ._retrieval.strength import apply_strength_to_normal
from ._retrieval.retrieval_clauses import (
    eligible_retrieval_clauses,
    select_retrieval_clause_hits,
)
from ._retrieval.supplemental_propositions import apply_semantic_single_supplement
from ._retrieval.user_level_l0 import collapse_user_level_l0_hits, require_user_scope
from ._retrieval.profile_owner_gate import gate_l6_schemas
from .specific_supplement import (
    is_internal_profile_chain_hit,
)
from ._retrieval.candidate_guards import (
    apply_post_bge_fusion_reserve,
    ensure_unique_top_k,
    nominate_lexical_after_mmr,
    nominate_multi_tag_candidates,
    rerank_with_guarded_nominee,
    rerank_with_lexical_survival,
)
from ._retrieval.topic_candidates import (
    apply_topic_graph_candidates,
    apply_topic_overlay,
    is_collection_query,
    query_subjects as extract_topic_query_subjects,
    same_series_bundle_candidates,
    select_episode_bundle,
    select_same_series_bundle,
    select_topic_bundle,
    topic_graph_candidates,
    topic_overlay_candidates,
)
from ._retrieval.trace import ReadTraceLogger

logger = logging.getLogger(__name__)

# Search 的 Schema 路只公开 L6。L0 仍可作为 owner gate / supplemental
# proposition 的隐藏上下文使用，但只由 get_user_profile() 对外返回。
_HIDDEN_L0_LAYERS = [MemoryLayer.L0_BASIC_INFO]
_VDB_HIDDEN_L0_LAYERS = [MemoryLayer.L0_BASIC_INFO]

# L0 隐藏上下文召回候选上限（basic_info 是单条演化链，少量即可覆盖）。
_HIDDEN_L0_RECALL_LIMIT = 10

# 主 VDB 召回（语义 + BM25 关键词）覆盖的记忆层。
# 覆盖 L2/L3/L4：fact / summary / identity。identity（L4）按 VDB 普通记忆处理，
# 参与语义+BM25 融合排序，最终归入 normal 通道（不再算 profile）。
# 与隐藏 L0 / Graph L6 通道互斥，杜绝双重召回。
# 不含 L1_RAW（原始对话，append-only 不召回）、L5_KNOWLEDGE、L7（graph 专属通道）。
_VDB_RECALL_LAYERS = [
    MemoryLayer.L2_FACT,
    MemoryLayer.L3_SUMMARY,
    MemoryLayer.L4_IDENTITY,
]


def _graph_custom(hit: Dict[str, Any]) -> Dict[str, Any]:
    raw = hit.get("custom_json") or hit.get("custom") or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, json.JSONDecodeError):
            return {}
    return {}


def select_schema_results(
    fused_l6: List[Dict[str, Any]],
    *,
    schema_limit: int,
    explicit_state_reserve: int = 0,
) -> List[Dict[str, Any]]:
    """Apply the public L6-only schema limit with explicit-state reserves."""
    results = list(fused_l6)
    if schema_limit == 0:
        return []
    if schema_limit < 0 or len(results) <= schema_limit:
        return results

    selected = results[:schema_limit]
    reserve = max(0, int(explicit_state_reserve))
    selected_ids = {item.get("node_id") for item in selected}
    missing_explicit = [
        item for item in fused_l6
        if item.get("_schema_kind") == "explicit_state"
        and item.get("node_id") not in selected_ids
    ][:reserve]
    for item in missing_explicit:
        replace_at = next(
            (
                index for index in range(len(selected) - 1, -1, -1)
                if selected[index].get("_schema_kind") != "explicit_state"
            ),
            None,
        )
        if replace_at is None:
            break
        selected[replace_at] = item
    return selected


def select_promoted_normal_facts(
    vdb_scored: List[Dict[str, Any]],
    *,
    min_score: float,
    reserve: int,
    excluded_node_ids: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    """Pick query-relevant L2 facts promoted by nightly evoling into normal."""
    if reserve <= 0:
        return []
    selected = []
    seen = set(excluded_node_ids or set())
    ranked = sorted(
        vdb_scored,
        key=lambda item: max(
            float(item.get("score") or 0.0),
            float(item.get("_semantic") or 0.0),
            float(item.get("_semantic_admission") or 0.0),
        ),
        reverse=True,
    )
    for item in ranked:
        node = item.get("node")
        custom = (getattr(node, "custom", None) or {}) if node else {}
        node_id = item.get("node_id")
        # RRF deliberately compresses the final ranking score into a small
        # rank-based range.  The promoted-fact threshold was calibrated on raw
        # semantic cosine, so preserve that admission signal after blending.
        relevance = max(
            float(item.get("score") or 0.0),
            float(item.get("_semantic") or 0.0),
            float(item.get("_semantic_admission") or 0.0),
        )
        if (
            node_id and node_id not in seen
            and node is not None
            and getattr(node, "layer", None) == MemoryLayer.L2_FACT
            and bool(custom.get("s2_profile_eligible"))
            and relevance >= min_score
        ):
            selected.append({
                **item,
                "source": "s2_promoted_fact",
            })
            seen.add(node_id)
            if len(selected) >= reserve:
                break
    return selected


# ========================================================================
# Config helpers
# ========================================================================

def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _resolve_normal_min_score(value: Optional[float]) -> float:
    """Resolve the caller's normal-memory score floor without treating zero as absent.

    ``ReadRequest.min_score`` has a concrete public default, so an explicit
    ``0`` is meaningful and disables the normal-result score filter. ``None``
    is retained only as a defensive compatibility fallback for internal callers
    that construct requests outside the public typed API.
    """
    return 0.3 if value is None else max(0.0, float(value))


def _filter_normal_results_by_score(
    items: List[Dict[str, Any]], min_score: float,
) -> List[Dict[str, Any]]:
    """Filter normal candidates before diversity and bounded truncation."""
    gated_sources = {"vdb", "vdb_keyword_only", "temporal_day"}
    return [
        item for item in items
        if item.get("source") not in gated_sources
        or float(item.get("score") or 0.0) >= min_score
    ]


# ========================================================================
# Pipeline
# ========================================================================

class BaseReadPipeline(ReadPipeline):
    """
    Shared reader implementation. Subclasses choose the candidate recall strategy.
    """

    VERSION = "base"
    ENABLE_KEYWORD_RECALL = False

    def __init__(
        self,
        config: MemoryConfig,
        embed_service: Optional[EmbedService] = None,
        vector_store: Optional[VectorStoreBase] = None,
        graph_store: Optional[GraphStoreBase] = None,
        cache: Any = None,
    ):
        self.config = config
        self._embed_service = embed_service
        self._external_vector_store = vector_store
        self._graph_store = graph_store
        self._cache = cache
        self._vector_store: Optional[VectorStoreBase] = None
        self._vector_store_initialized = False
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        if self._embed_service is None:
            self._embed_service = EmbedService(self.config)
        self._vector_store = self._external_vector_store or create_vector_store(self.config)
        if self._external_vector_store and getattr(self._external_vector_store, "_client", None):
            self._vector_store_initialized = True
        self._initialized = True
        logger.debug("%s initialized", self.__class__.__name__)

    async def _keyword_search(self, vector_store, **kwargs) -> List[Dict[str, Any]]:
        """Return BM25 candidates only for readers that enable keyword recall."""
        if not self.ENABLE_KEYWORD_RECALL:
            return []
        return await vector_store.keyword_search(**kwargs)

    @property
    def embed_service(self) -> EmbedService:
        if self._embed_service is None:
            self._embed_service = EmbedService(self.config)
        return self._embed_service

    async def _get_vector_store(self) -> VectorStoreBase:
        if self._vector_store is None:
            self._vector_store = self._external_vector_store or create_vector_store(self.config)
        if not self._vector_store_initialized:
            await self._vector_store.initialize()
            self._vector_store_initialized = True
        return self._vector_store

    # ------------------------------------------------------------------
    # Main pipeline
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 记忆关系图重排（用法 A / B / C，见 docs/pro_graph_design.md §3）
    # ------------------------------------------------------------------

    def _graph_cfg(self):
        return getattr(self.config, "graph_store", None)

    def _graph_rerank_on(self) -> bool:
        gcfg = self._graph_cfg()
        return bool(
            self._graph_store is not None
            and gcfg is not None
            and getattr(gcfg, "graph_rerank_enabled", False)
        )

    def _episode_recall_on(self) -> bool:
        """Episode 框选**不依赖图** —— 它只要 episode_id 字段 + 一次 VDB 过滤查询。"""
        rcfg = getattr(self.config, "recall", None)
        return bool(rcfg is not None and getattr(rcfg, "episode_recall_enabled", False))

    def _expansion_on(self) -> bool:
        return self._graph_rerank_on() or self._episode_recall_on()

    async def _apply_graph_rerank(
        self,
        *,
        hits: List[Dict[str, Any]],
        vector_store,
        query_embedding: List[float],
        min_score: float,
        request: ReadRequest,
        dynamic_entity_tag_embed_enabled: bool = True,
        strict: bool = False,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
        """对**截断前**的召回池做图重排。

        图是严格附加的：任何一步失败都原样返回入参，退化成"没有图"的行为。

        Returns:
            (重排后的 hits（含用法 B 捞回的候选）, 关系路径, 观测统计)
        """
        gcfg = self._graph_cfg()
        pool = {
            it.get("node_id"): float(it.get("score", 0.0))
            for it in hits
            if it.get("node_id")
        }
        if len(pool) < 2:
            return hits, [], {}

        try:
            from ._graph.rerank import (
                build_adjacency, collect_directed_edges, propagate_support,
                relax_admission, find_relation_paths, filter_relation_edges,
                annotate_same_series_in_pool, merge_expansions_into_seeds,
                tokenize_query_terms, keyword_content_overlap, tag_keyword_overlap,
                temporal_affinity_factor,
            )

            # 图边是可选的：episode 框选只要 episode_id 字段，graph 关掉时这里为空，
            # 用法 A（池内传播）自然跳过，用法 B 仍能用 episode 候选跑起来。
            edges = []
            if self._graph_rerank_on():
                edges = await self._graph_store.get_related_edges(
                    list(pool),
                    include_sources=request.include_sources,
                    exclude_sources=request.exclude_sources,
                ) or []
                edges = filter_relation_edges(
                    edges,
                    include_same_series=bool(getattr(
                        self.config.recall,
                        "same_series_timeline_enabled",
                        False,
                    )),
                )
            if not edges and not self._episode_recall_on():
                return hits, [], {"edges": 0}
            adjacency = build_adjacency(edges) if edges else {}
            forward_dirs = collect_directed_edges(edges) if edges else {}
            if adjacency:
                hits = annotate_same_series_in_pool(hits, adjacency)

            # ── 用法 A：池内支持度传播（不引入新候选）──
            _moved = 0
            if adjacency:
                new_scores, support = propagate_support(
                    pool, adjacency,
                    beta=getattr(gcfg, "propagate_beta", 0.15),
                    iterations=getattr(gcfg, "propagate_iters", 1),
                    support_cap=getattr(gcfg, "support_cap", 1.0),
                )
                for it in hits:
                    nid = it.get("node_id")
                    if nid in new_scores and new_scores[nid] != pool.get(nid):
                        it["score"] = new_scores[nid]
                        it["graph_support"] = round(support.get(nid, 0.0), 6)
                        _moved += 1
            else:
                new_scores, support = dict(pool), {}

            stats: Dict[str, Any] = {
                "edges": len(edges),
                "propagated": _moved,
                "beta": getattr(gcfg, "propagate_beta", 0.15),
            }

            # ── 用法 B：扩展 + 硬闸 + 多通道打分 +（可选）并进 seed ──
            #
            # 扩展**无条件**发生（不做 query 意图 / scene judge），由打分决定谁浮上来。
            #
            #   ① 度数归一化
            #   ② k 跳 BFS + 多 seed 佐证
            #   ②b episode 共属（有界叠加）
            #   ③ 时间亲和（乘进 support）
            #   ④ 类型配额 + 截 top-N
            #   ⑤ 批量取向量算 sim
            #   ⑥ 硬闸：sim ≥ seed_sim − δ
            #   ⑦ 多通道分：语义 + BM25 + tag∩keyword + 时序 + co_occur/结构
            #   ⑧ 通过闸的扩展按时间并进 seed（\\n\\n），不单独占坑
            admitted_hits: List[Dict[str, Any]] = []
            gamma = getattr(gcfg, "relax_gamma", 0.3)
            if gamma > 0:
                from ._graph.rerank import (
                    apply_temporal_affinity, apply_type_quota, blend_support,
                    normalize_by_degree, reachable_within,
                )
                from ._graph.projector import resolve_memory_times

                _hops = getattr(gcfg, "expand_hops", 2)
                _seed_floor = getattr(gcfg, "seed_floor", 0.6)
                _top_n = getattr(gcfg, "expand_prefilter_top_n", 50)
                _delta = getattr(gcfg, "seed_sim_delta", 0.2)
                _merge = getattr(gcfg, "expand_merge_into_seed", True)

                # ① + ②
                _msb = getattr(gcfg, "multi_seed_bonus", 0.5)
                reach = reachable_within(
                    new_scores, normalize_by_degree(adjacency),
                    hops=_hops, seed_floor=_seed_floor, multi_seed_bonus=_msb,
                )
                stats["reachable"] = len(reach)

                # ②b Episode 共属
                _ep = await self._episode_candidates(
                    vector_store, hits, new_scores, request, _seed_floor, _msb,
                    strict=strict,
                )
                if _ep:
                    reach = blend_support(
                        reach, _ep,
                        lam=getattr(gcfg, "episode_blend_lambda", 0.3),
                        support_cap=getattr(gcfg, "support_cap", 1.0),
                    )
                    stats["episode_candidates"] = len(_ep)

                _times: Dict[str, float] = {}
                if reach:
                    _t_ids = list(set(reach) | set(new_scores))
                    _times = await resolve_memory_times(vector_store, _t_ids)
                    reach = apply_temporal_affinity(
                        reach, times=_times,
                        tau_days=getattr(gcfg, "temporal_tau_days", 7.0),
                        floor=getattr(gcfg, "temporal_affinity_floor", 0.3),
                    )
                    reach = apply_type_quota(reach, top_n=_top_n)
                    stats["prefiltered"] = len(reach)

                if reach:
                    cand_sims = await self._candidate_similarities(
                        vector_store, list(reach), query_embedding,
                    )
                    # 通道特征：BM25(content) / tag∩keyword / 时序（相对 via_seed）
                    q_terms = tokenize_query_terms(request.query or "")
                    cand_nodes = await vector_store.get_by_ids(list(reach))
                    by_id = {
                        n.node_id: n for n in (cand_nodes or []) if n is not None
                    }
                    cand_bm25: Dict[str, float] = {}
                    cand_tag: Dict[str, float] = {}
                    cand_temporal: Dict[str, float] = {}
                    _tau = getattr(gcfg, "temporal_tau_days", 7.0)
                    _tfloor = getattr(gcfg, "temporal_affinity_floor", 0.3)
                    for cid, (_sup, via, _rtype, _hop) in reach.items():
                        node = by_id.get(cid)
                        content = getattr(node, "content", "") if node else ""
                        tags = list(getattr(node, "tags", None) or []) if node else []
                        cand_bm25[cid] = keyword_content_overlap(q_terms, content)
                        # 先写词面 overlap；下面批量用实体 tag 向量 cos 取 max 覆盖
                        cand_tag[cid] = tag_keyword_overlap(q_terms, tags)
                        cand_temporal[cid] = temporal_affinity_factor(
                            _times.get(cid), _times.get(via),
                            tau_days=_tau, floor=_tfloor,
                        )

                    # expand tag 向量通道：实体 tags embed ↔ query embedding，与词面取 max
                    try:
                        ent_texts: List[str] = []
                        ent_owner: Dict[str, List[str]] = {}
                        for cid in list(reach.keys()):
                            node = by_id.get(cid)
                            if not node:
                                continue
                            tags = list(getattr(node, "tags", None) or [])
                            ners = list(getattr(node, "tag_ners", None) or [])
                            for t_text, _lab in entity_tag_pairs(tags, ners if ners else None):
                                ent_owner.setdefault(t_text, []).append(cid)
                                if t_text not in ent_texts:
                                    ent_texts.append(t_text)
                        stats["dynamic_entity_tag_embed_enabled"] = bool(
                            dynamic_entity_tag_embed_enabled
                        )
                        stats["dynamic_entity_tag_text_count"] = len(ent_texts)
                        stats["dynamic_entity_tag_vector_count"] = 0
                        if (
                            dynamic_entity_tag_embed_enabled
                            and ent_texts
                            and query_embedding is not None
                        ):
                            tvecs = await self.embed_service.embed_batch(ent_texts)
                            stats["dynamic_entity_tag_vector_count"] = sum(
                                bool(vector) for vector in (tvecs or [])
                            )
                            qn = sum(x * x for x in query_embedding) ** 0.5 or 1.0
                            for t_text, tv in zip(ent_texts, tvecs or []):
                                if not tv:
                                    continue
                                en = sum(x * x for x in tv) ** 0.5 or 1.0
                                c = sum(
                                    a * b for a, b in zip(query_embedding, tv)
                                ) / (qn * en)
                                for cid in ent_owner.get(t_text, []):
                                    if c > cand_tag.get(cid, 0.0):
                                        cand_tag[cid] = float(c)
                    except Exception as _e:
                        if strict:
                            raise
                        logger.debug("[%s] expand tag-vec failed: %s", self.VERSION, _e)

                    channel_weights = {
                        "sem": getattr(gcfg, "expand_w_sem", 1.0),
                        "bm25": getattr(gcfg, "expand_w_bm25", 0.3),
                        "tag": getattr(gcfg, "expand_w_tag", 0.5),
                        "temporal": getattr(gcfg, "expand_w_temporal", 0.2),
                        "co_occur": getattr(gcfg, "expand_w_co_occur", 0.15),
                        "struct": getattr(gcfg, "expand_w_struct", 0.25),
                    }
                    admitted = relax_admission(
                        seed_scores=new_scores,
                        adjacency=adjacency,
                        candidate_sims=cand_sims,
                        min_score=min_score,
                        gamma=gamma,
                        seed_floor=_seed_floor,
                        max_admit=getattr(gcfg, "max_expand", 20),
                        hops=_hops,
                        boost_beta=getattr(gcfg, "expand_boost_beta", 0.1),
                        reach=reach,
                        seed_sim_delta=_delta,
                        candidate_bm25=cand_bm25,
                        candidate_tag=cand_tag,
                        candidate_temporal=cand_temporal,
                        channel_weights=channel_weights,
                    )
                    admitted_hits = await self._materialize_admitted(
                        vector_store, admitted,
                    )
                    stats["admitted"] = len(admitted_hits)
                    stats["seed_sim_delta"] = _delta
                    stats["admitted_ids"] = [
                        a.get("node_id") for a in admitted_hits if a.get("node_id")
                    ]
                    stats["merge_into_seed"] = bool(_merge)

                    if _merge and admitted_hits:
                        hits = merge_expansions_into_seeds(hits, admitted_hits)
                        stats["merged_seeds"] = sum(
                            1 for h in hits if h.get("graph_merged")
                        )
                        admitted_hits = []  # 已并进 seed，不再追加独立 hit

            # ── 用法 C：关系路径（独立通道）──
            paths: List[Dict[str, Any]] = []
            if getattr(gcfg, "path_enabled", True):
                paths = find_relation_paths(
                    hit_scores=new_scores,
                    adjacency=adjacency,
                    high_floor=getattr(gcfg, "path_high_floor", 0.6),
                    forward_dirs=forward_dirs,
                )
                paths = await self._decorate_paths(vector_store, paths)
                stats["paths"] = len(paths)

            return hits + admitted_hits, paths, stats
        except Exception as e:
            if strict:
                raise
            logger.warning("[%s] graph rerank failed (non-fatal): %s", self.VERSION, e)
            return hits, [], {}

    async def _episode_candidates(
        self,
        vector_store,
        hits: List[Dict[str, Any]],
        scores: Dict[str, float],
        request: ReadRequest,
        seed_floor: float,
        multi_seed_bonus: float,
        *,
        strict: bool = False,
    ) -> Dict[str, Tuple[float, str, str, int]]:
        """用 episode_id 框出"同一次讨论"的候选，折算成结构支持度进同一漏斗。

        Episode 是 SDK 内部按写入时间间隔切出的一次讨论（跨 write 批次）。
        同一 episode 的记忆彼此有真实关联但向量可能互不相似 —— 正是向量检索找不到的那部分。
        用字段框选而不是建边：一个 episode 10-30 条，两两连边是 45-435 条；
        也因此**不依赖图**，`MEMORY_ENABLE_GRAPH=false` 时同样可用。
        """
        rcfg = getattr(self.config, "recall", None)
        if rcfg is None or not getattr(rcfg, "episode_recall_enabled", False):
            return {}
        try:
            from ._graph.rerank import episode_support

            # 只从高分命中取 episode，避免从噪声里框候选
            eps = {
                (getattr(h.get("node"), "episode_id", "") or "")
                for h in hits
                if h.get("node") is not None
                and float(h.get("score", 0.0)) >= seed_floor
            }
            eps.discard("")
            if not eps:
                return {}

            _uid = request.user_id or (
                request.user_ids[0] if request.user_ids else None
            )
            members = await vector_store.list_by_episode(
                list(eps), user_id=_uid, layers=_VDB_RECALL_LAYERS,
                include_sources=request.include_sources,
                exclude_sources=request.exclude_sources,
            )
            by_ep: Dict[str, List[str]] = {}
            for n in (members or []):
                ep = getattr(n, "episode_id", "") or ""
                if ep:
                    by_ep.setdefault(ep, []).append(n.node_id)
            if not by_ep:
                return {}

            # 更新 hits 里的分数视图（传播后的分数），供 seed 选择使用
            _hits_view = [
                {**h, "score": scores.get(h.get("node_id"), h.get("score", 0.0))}
                for h in hits
            ]
            return episode_support(
                _hits_view, by_ep,
                seed_floor=seed_floor,
                weight=getattr(rcfg, "episode_recall_weight", 0.7),
                multi_seed_bonus=multi_seed_bonus,
            )
        except Exception as e:
            if strict:
                raise
            logger.debug(f"[episode] candidates failed (non-fatal): {e}")
            return {}

    @staticmethod
    async def _candidate_similarities(
        vector_store, cand_ids: List[str], query_embedding: List[float],
    ) -> Dict[str, float]:
        """算候选与 query 的余弦。用库里存的向量，不重新 embed。"""
        if not cand_ids or not query_embedding:
            return {}
        embs = await vector_store.get_embeddings(cand_ids)
        qn = sum(x * x for x in query_embedding) ** 0.5
        if qn == 0:
            return {}
        out: Dict[str, float] = {}
        for nid, emb in (embs or {}).items():
            if not emb:
                continue
            en = sum(x * x for x in emb) ** 0.5
            if en == 0:
                continue
            out[nid] = sum(a * b for a, b in zip(query_embedding, emb)) / (qn * en)
        return out

    async def _dynamic_episode_member_candidates(
        self,
        vector_store,
        episode_hits: List[Dict[str, Any]],
        query_embedding: List[float],
        *,
        existing_ids: Set[str],
        total_limit: int,
        per_episode: int,
        min_similarity: float,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Materialize query-relevant L2 members from compact L3 containers.

        L3 only nominates a bounded group. Member/query cosine orders that group;
        the normal local reranker makes the final top-k decision. No episode score
        is added to an L2 score, and no L3 body enters the answer context.
        """
        if not episode_hits or total_limit <= 0:
            return [], {"episodes": 0, "member_ids": [], "added": 0}

        episode_members: List[Tuple[str, List[str]]] = []
        all_member_ids: List[str] = []
        seen_member_ids = set()
        for episode in episode_hits:
            node = episode.get("node")
            custom = getattr(node, "custom", None) or {}
            member_ids = []
            for member_id in custom.get("member_memory_ids") or []:
                member_id = str(member_id or "")
                if not member_id or member_id in seen_member_ids:
                    continue
                seen_member_ids.add(member_id)
                member_ids.append(member_id)
                all_member_ids.append(member_id)
            if member_ids:
                episode_members.append((str(episode.get("node_id") or ""), member_ids))
        if not all_member_ids:
            return [], {
                "episodes": len(episode_hits), "member_ids": [], "added": 0,
            }

        nodes, similarities = await asyncio.gather(
            vector_store.get_by_ids(all_member_ids),
            self._candidate_similarities(
                vector_store, all_member_ids, query_embedding,
            ),
        )
        by_id = {
            node.node_id: node for node in (nodes or [])
            if node is not None
            and getattr(node, "layer", None) == MemoryLayer.L2_FACT
            and getattr(getattr(node, "status", None), "value", "")
                == MemoryStatus.ACTIVE.value
            and bool(getattr(node, "is_latest", True))
        }

        candidates: List[Dict[str, Any]] = []
        selected_ids = set(existing_ids)
        from ..utils.keyword_ties import memory_content_key
        for episode_id, member_ids in episode_members:
            ranked = sorted(
                (
                    (float(similarities.get(member_id, -1.0)), member_id)
                    for member_id in member_ids
                    if member_id in by_id and member_id not in selected_ids
                ),
                key=lambda row: (-row[0], memory_content_key({"node": by_id[row[1]]})),
            )
            accepted = 0
            for similarity, member_id in ranked:
                if similarity < min_similarity:
                    continue
                selected_ids.add(member_id)
                candidates.append({
                    "node_id": member_id,
                    "node": by_id[member_id],
                    "score": similarity,
                    "source": "episode_member_expansion",
                    "_semantic": similarity,
                    "_episode_parent_id": episode_id,
                    "_episode_member_similarity": similarity,
                })
                accepted += 1
                if accepted >= per_episode or len(candidates) >= total_limit:
                    break
            if len(candidates) >= total_limit:
                break
        return candidates, {
            "episodes": len(episode_members),
            "considered_members": len(all_member_ids),
            "member_ids": [item["node_id"] for item in candidates],
            "added": len(candidates),
            "per_episode": per_episode,
            "min_similarity": min_similarity,
        }

    async def _anchored_episode_member_candidates(
        self,
        vector_store,
        seed_items: List[Dict[str, Any]],
        query_embedding: List[float],
        *,
        existing_ids: Set[str],
        episode_limit: int,
        total_limit: int,
        per_episode: int,
        seed_min_similarity: float,
        member_min_similarity: float,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """Expand siblings only from already relevant L2 episode anchors."""
        anchors: List[Tuple[str, str]] = []
        seen_parents = set()
        for item in seed_items:
            node = item.get("node")
            if node is None or getattr(node, "layer", None) != MemoryLayer.L2_FACT:
                continue
            node_id = str(item.get("node_id") or getattr(node, "node_id", "") or "")
            if not node_id:
                continue
            for parent_id in parent_episode_ids(item):
                if parent_id in seen_parents:
                    continue
                seen_parents.add(parent_id)
                anchors.append((node_id, parent_id))
                if len(anchors) >= episode_limit:
                    break
            if len(anchors) >= episode_limit:
                break
        if not anchors:
            return [], {"mode": "anchored", "anchors": 0, "member_ids": [], "added": 0}

        parents = await vector_store.get_by_ids([parent_id for _, parent_id in anchors])
        parent_by_id = {
            node.node_id: node for node in (parents or [])
            if node is not None
            and getattr(node, "layer", None) == MemoryLayer.L3_SUMMARY
            and (getattr(node, "custom", None) or {}).get("summary_kind")
                in {"dynamic_episode", "series_episode"}
        }
        members_by_parent: Dict[str, List[str]] = {}
        all_ids = [seed_id for seed_id, _ in anchors]
        for _, parent_id in anchors:
            parent = parent_by_id.get(parent_id)
            member_ids = [
                str(member_id or "")
                for member_id in ((getattr(parent, "custom", None) or {}).get(
                    "member_memory_ids"
                ) or [])
                if member_id
            ] if parent is not None else []
            members_by_parent[parent_id] = member_ids
            all_ids.extend(member_ids)
        all_ids = list(dict.fromkeys(all_ids))
        similarities = await self._candidate_similarities(
            vector_store, all_ids, query_embedding,
        )

        eligible_parents = [
            (seed_id, parent_id)
            for seed_id, parent_id in anchors
            if float(similarities.get(seed_id, -1.0)) >= seed_min_similarity
        ]
        candidate_ids = list(dict.fromkeys(
            member_id
            for _, parent_id in eligible_parents
            for member_id in members_by_parent.get(parent_id, [])
            if member_id not in existing_ids
        ))
        nodes = await vector_store.get_by_ids(candidate_ids)
        by_id = {
            node.node_id: node for node in (nodes or [])
            if node is not None
            and getattr(node, "layer", None) == MemoryLayer.L2_FACT
            and getattr(getattr(node, "status", None), "value", "")
                == MemoryStatus.ACTIVE.value
            and bool(getattr(node, "is_latest", True))
        }

        candidates: List[Dict[str, Any]] = []
        selected_ids = set(existing_ids)
        used_anchors = []
        for seed_id, parent_id in eligible_parents:
            ranked = sorted(
                (
                    (float(similarities.get(member_id, -1.0)), member_id)
                    for member_id in members_by_parent.get(parent_id, [])
                    if member_id in by_id and member_id not in selected_ids
                ),
                key=lambda row: (-row[0], row[1]),
            )
            accepted = 0
            for similarity, member_id in ranked:
                if similarity < member_min_similarity:
                    continue
                selected_ids.add(member_id)
                candidates.append({
                    "node_id": member_id,
                    "node": by_id[member_id],
                    "score": similarity,
                    "source": "episode_member_expansion",
                    "_semantic": similarity,
                    "_episode_parent_id": parent_id,
                    "_episode_anchor_id": seed_id,
                    "_episode_member_similarity": similarity,
                })
                accepted += 1
                if accepted >= per_episode or len(candidates) >= total_limit:
                    break
            if accepted:
                used_anchors.append(seed_id)
            if len(candidates) >= total_limit:
                break
        return candidates, {
            "mode": "anchored",
            "anchors": len(anchors),
            "eligible_anchors": len(eligible_parents),
            "anchor_ids": used_anchors,
            "considered_members": len(candidate_ids),
            "member_ids": [item["node_id"] for item in candidates],
            "added": len(candidates),
            "per_episode": per_episode,
            "seed_min_similarity": seed_min_similarity,
            "min_similarity": member_min_similarity,
        }

    @staticmethod
    async def _materialize_admitted(
        vector_store, admitted: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """把用法 B 准入的候选补成标准 hit（回 VDB 取 node，顺带过滤已删/不可见的）。"""
        if not admitted:
            return []
        nodes = await vector_store.get_by_ids([a["node_id"] for a in admitted])
        by_id = {n.node_id: n for n in (nodes or []) if n is not None}
        out: List[Dict[str, Any]] = []
        for a in admitted:
            node = by_id.get(a["node_id"])
            if node is None:
                continue
            # 图里可能还留着已被逻辑删除的节点，这里以 VDB 的 status 为准
            if getattr(node, "status", None) is not None and \
                    getattr(node.status, "value", "") == MemoryStatus.SHADOW.value:
                continue
            out.append({
                "node_id": a["node_id"],
                "score": a["score"],
                "node": node,
                "source": "graph_relaxed",
                "graph_support": round(a["support"], 6),
                "graph_relaxed_threshold": round(a["threshold"], 6),
                "graph_via": a["via_seed"],
                "graph_relation": a["relation_type"],
            })
        return out

    @staticmethod
    async def _decorate_paths(
        vector_store, paths: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """给关系路径补上节点内容 —— 路径要交给答案 LLM 读，光有 id 没用。"""
        if not paths:
            return []
        all_ids = {nid for p in paths for nid in p.get("nodes", [])}
        nodes = await vector_store.get_by_ids(list(all_ids))
        by_id = {n.node_id: n for n in (nodes or []) if n is not None}
        out: List[Dict[str, Any]] = []
        for p in paths:
            steps = []
            for i, nid in enumerate(p.get("nodes", [])):
                node = by_id.get(nid)
                if node is None:
                    steps = []
                    break
                steps.append({"memory_id": nid, "content": node.content})
                if i < len(p.get("relations", [])):
                    rel = p["relations"][i]
                    step = {"relation": rel.get("type")}
                    # 有向边（temporal_next）带方向，否则"X 之后"和"之前"分不开
                    if rel.get("direction"):
                        step["direction"] = rel["direction"]
                    steps.append(step)
            if steps:
                out.append({
                    "score": p["score"], "hops": p["hops"], "path": steps,
                })
        return out

    async def _recall_user_level_l0(
        self, vector_store, request,
    ) -> List[Dict[str, Any]]:
        """Load deterministic user-level L0 context without semantic gating."""
        user_scope = require_user_scope(
            request.user_ids, [request.user_id] if request.user_id else None,
        )
        if not user_scope:
            return []
        nodes = await vector_store.list_by_user(
            user_ids=user_scope,
            layers=_VDB_HIDDEN_L0_LAYERS,
            limit=_HIDDEN_L0_RECALL_LIMIT,
            status_filter=[MemoryStatus.ACTIVE],
            only_latest=False,
            with_vectors=False,
            include_sources=request.include_sources,
            exclude_sources=request.exclude_sources,
        )
        return [
            {"node_id": node.node_id, "node": node, "score": 1.0,
             "retrieval_source": "hidden_l0_context"}
            for node in nodes
        ]

    async def read(
        self,
        request: ReadRequest,
        ctx: Optional[PipelineContext] = None,
        tracer: Optional[PipelineTracer] = None,
    ) -> ReadResponse:
        """Optionally fill an underfull result without replacing selected facts.

        The default path is unchanged. The bounded second pass reuses the query
        vector and disables side-channel output, so intention expiry cannot run twice.
        Only its early per-channel cosine gate is deferred; the final gate stays.
        """
        enabled = os.getenv("MEMORY_HYBRID_V2_FILL_UNDERFLOW", "0").strip().lower() in {"1", "true", "yes", "on"}
        query_capture = {} if enabled else None
        response = await self._read_once(request, ctx, tracer, _query_capture=query_capture)
        limit = request.limit if request.limit > 0 else 10
        session_episode_context_applied = bool(
            (response.extra or {}).get("session_episode_context_applied", False)
        )
        if (not enabled or session_episode_context_applied or response.error_code
                or not response.success or normal_count(response.memories) >= limit):
            return response
        vector = (query_capture or {}).get("vector")
        if not vector:
            return response
        floor = _resolve_normal_min_score(request.min_score)
        started = datetime.now()
        extra_request = replace(
            request, query_embedding=vector, schema_limit=0, intention_limit=0,
            request_id=(request.request_id + ":underflow") if request.request_id else "",
            extra=dict(request.extra or {}),
        )
        try:
            timeout = max(0.1, _float_env("MEMORY_HYBRID_V2_FILL_TIMEOUT_SECONDS", 10.0))
            fallback = await asyncio.wait_for(
                self._read_once(extra_request, ctx, None, _defer_semantic_gate=True),
                timeout=timeout,
            )
            if fallback.error_code or not fallback.success:
                response.extra["underflow_fill"] = {"status": "failed", "added": 0, "error_code": fallback.error_code}
            else:
                previous = len(response.memories)
                response.memories = fill_normal_slots(response.memories, fallback.memories, limit=limit, min_score=floor)
                response.total_found = len(response.memories)
                response.extra["underflow_fill"] = {"status": "complete", "added": len(response.memories) - previous}
        except asyncio.TimeoutError:
            response.extra["underflow_fill"] = {"status": "timeout", "added": 0}
        except Exception as exc:
            logger.warning("[%s] underflow fill failed: %s", self.VERSION, exc)
            response.extra["underflow_fill"] = {"status": "failed", "added": 0, "error_type": type(exc).__name__}
        response.elapsed_ms += (datetime.now() - started).total_seconds() * 1000
        return response

    async def _read_once(
        self,
        request: ReadRequest,
        ctx: Optional[PipelineContext] = None,
        tracer: Optional[PipelineTracer] = None,
        *,
        _defer_semantic_gate: bool = False,
        _query_capture: Optional[Dict[str, Any]] = None,
    ) -> ReadResponse:
        start_time = datetime.now()
        response = ReadResponse()

        if tracer is None:
            tracer = create_tracer(
                operation="read",
                pipeline_version=self.VERSION,
                uid=request.user_id,
                agent_id=request.agent_id,
                content_preview=request.query,
            )

        # Read-side trace logger
        trace_log = ReadTraceLogger(
            cache=self._cache,
            request_id=request.request_id,
            user_id=request.user_id or (request.user_ids[0] if request.user_ids else ""),
            agent_id=request.agent_id or "default_agent",
            reader_version=self.VERSION,
            session_id=request.session_id or (request.session_ids[0] if len(request.session_ids) == 1 else ""),
        )

        # Pre-declare for exception safety
        vdb_semantic_hits: List[Dict[str, Any]] = []
        vdb_keyword_hits: List[Dict[str, Any]] = []
        hidden_l0_hits: List[Dict[str, Any]] = []
        graph_schema_hits: List[Dict[str, Any]] = []
        graph_intention_hits: List[Dict[str, Any]] = []

        try:
            if not request.query:
                response.error_code = 400
                response.error_message = "query is required"
                return response

            # --- Load config ---
            # Additive fusion（按条加成，不再全局 has_bm25 把无 BM25 的 sem×0.6）:
            #   final = sem + 𝟙[bm25>0]·W_BM25·bm25 + 𝟙[tag_cos>0]·W_TAG·tag_cos
            W_BM25 = _float_env("MEMORY_HYBRID_V2_VDB_WEIGHT_BM25", 0.35)
            W_TAG = _float_env("MEMORY_HYBRID_V2_VDB_WEIGHT_TAG", 0.3)
            TAG_COS_THR = _float_env("MEMORY_HYBRID_V2_TAG_COS_THR", 0.5)
            DYNAMIC_ENTITY_TAG_EMBED_ON = bool(
                getattr(
                    self.config.recall,
                    "dynamic_entity_tag_embed_enabled",
                    False,
                )
            )
            TAG_INDEX_NOMINEE_ON = bool(getattr(
                self.config.recall, "tag_index_nominee_enabled", False
            ))
            TAG_INDEX_NOMINEE_TOPK = max(1, int(getattr(
                self.config.recall, "tag_index_nominee_topk", 10
            )))
            TAG_INDEX_NOMINEE_MIN_SCORE = float(getattr(
                self.config.recall, "tag_index_nominee_min_score", 0.4
            ))
            TAG_INDEX_NOMINEE_MIN_MATCHES = max(1, int(getattr(
                self.config.recall, "tag_index_nominee_min_matches", 2
            )))
            TAG_INDEX_NOMINEE_SEARCH_LIMIT = max(1, int(getattr(
                self.config.recall, "tag_index_nominee_search_limit", 40
            )))
            TAG_INDEX_NOMINEE_LIMIT = max(0, int(getattr(
                self.config.recall, "tag_index_nominee_limit", 1
            )))
            TAG_INDEX_NOMINEE_WEIGHT = max(0.0, float(getattr(
                self.config.recall, "tag_index_nominee_weight", 0.3
            )))
            TAG_INDEX_NOMINEE_MAX_TAG_DF = max(1, int(getattr(
                self.config.recall, "tag_index_nominee_max_tag_df", 8
            )))
            GEO_INTENT_PRIOR = _float_env("MEMORY_HYBRID_V2_GEO_INTENT_PRIOR", 0.6)
            EV_BOOST_MAX = _float_env("MEMORY_HYBRID_V2_EVIDENCE_BOOST_MAX", 0.3)
            EV_SATURATE = _int_env("MEMORY_HYBRID_V2_EVIDENCE_SATURATE", 5)
            # keyword-only 命中：给虚 sem 底，避免 0.2·bm25 过不了 MIN_SCORE（P1）
            KW_ONLY_SEM_FLOOR = _float_env("MEMORY_HYBRID_V2_KW_ONLY_SEM_FLOOR", 0.4)
            RRF_BLEND = _float_env("MEMORY_HYBRID_V2_RRF_BLEND", 0.4)
            # Light-search temporal / diversity（零 LLM；env 可关）
            TEMPORAL_ADMIT = _float_env("MEMORY_HYBRID_V2_TEMPORAL_ADMIT_PRIOR", 0.55)
            TEMPORAL_BOOST = _float_env("MEMORY_HYBRID_V2_TEMPORAL_BOOST", 0.18)
            TEMPORAL_MONTH_BOOST = _float_env("MEMORY_HYBRID_V2_TEMPORAL_MONTH_BOOST", 0.08)
            TEMPORAL_LIST_LIMIT = _int_env("MEMORY_HYBRID_V2_TEMPORAL_LIST_LIMIT", 2000)
            TEMPORAL_ADMIT_CAP = _int_env("MEMORY_HYBRID_V2_TEMPORAL_ADMIT_CAP", 24)
            DIVERSITY_ON = os.environ.get(
                "MEMORY_HYBRID_V2_DIVERSITY", "1"
            ).strip().lower() not in {"0", "false", "no", "off"}
            SOCIAL_BOILER_PENALTY = _float_env(
                "MEMORY_HYBRID_V2_SOCIAL_BOILER_PENALTY", 0.25
            )
            POOL_DF_THR = _float_env("MEMORY_HYBRID_V2_POOL_DF_THR", 0.4)
            POOL_DF_PENALTY = _float_env("MEMORY_HYBRID_V2_POOL_DF_PENALTY", 0.18)
            NEAR_DUP_THR = _float_env("MEMORY_HYBRID_V2_NEAR_DUP_JACCARD", 0.5)
            NEAR_DUP_PENALTY = _float_env("MEMORY_HYBRID_V2_NEAR_DUP_PENALTY", 0.4)
            MMR_LAMBDA = _float_env("MEMORY_HYBRID_V2_MMR_LAMBDA", 0.55)
            SESSION_EPISODE_CONTEXT_ON = bool(
                getattr(self.config.recall, "session_episode_context_enabled", False)
            )
            SESSION_EPISODE_LIMIT = int(
                getattr(self.config.recall, "session_episode_limit", 5) or 5
            )
            SESSION_EPISODE_FACT_LIMIT = int(
                getattr(self.config.recall, "session_episode_fact_limit", 3) or 3
            )
            EPISODE_CAPSULE_ON = bool(
                getattr(self.config.recall, "episode_capsule_context_enabled", False)
            )
            EPISODE_CAPSULE_QUERY_MIN = float(
                getattr(self.config.recall, "episode_capsule_query_min", 0.65)
            )
            EPISODE_CAPSULE_ASSIGNMENT_MIN = float(
                getattr(self.config.recall, "episode_capsule_assignment_min", 0.60)
            )
            EPISODE_CAPSULE_FIRST_RANK_MAX = int(
                getattr(self.config.recall, "episode_capsule_first_rank_max", 3)
            )
            EPISODE_CAPSULE_MEMBER_LIMIT = int(
                getattr(self.config.recall, "episode_capsule_member_limit", 3)
            )
            EPISODE_CAPSULE_RENDER_MODE = str(
                getattr(self.config.recall, "episode_capsule_render_mode", "full")
                or "full"
            ).strip().lower()
            EPISODE_MEMBER_EXPANSION_ON = bool(
                getattr(self.config.recall, "episode_member_expansion_enabled", False)
            )
            EPISODE_MEMBER_MODE = str(getattr(
                self.config.recall,
                "episode_member_expansion_mode",
                "anchored_attach",
            ) or "anchored_attach").strip().lower()
            EPISODE_MEMBER_EPISODE_LIMIT = max(1, int(
                getattr(
                    self.config.recall,
                    "episode_member_expansion_episode_limit",
                    3,
                ) or 3
            ))
            EPISODE_MEMBER_LIMIT = max(1, int(
                getattr(
                    self.config.recall,
                    "episode_member_expansion_member_limit",
                    6,
                ) or 6
            ))
            EPISODE_MEMBER_PER_EPISODE = max(1, int(
                getattr(
                    self.config.recall,
                    "episode_member_expansion_per_episode",
                    3,
                ) or 3
            ))
            EPISODE_MEMBER_EPISODE_MIN_SIM = float(getattr(
                self.config.recall,
                "episode_member_expansion_episode_min_similarity",
                0.35,
            ))
            EPISODE_MEMBER_MIN_SIM = float(getattr(
                self.config.recall,
                "episode_member_expansion_min_similarity",
                0.20,
            ))
            SAME_SERIES_TIMELINE_ON = bool(
                getattr(self.config.recall, "same_series_timeline_enabled", False)
            )
            LOCAL_RERANK_ON = bool(
                getattr(self.config.recall, "local_rerank_enabled", False)
            )
            LOCAL_RERANK_POOL_FACTOR = max(1, int(
                getattr(self.config.recall, "local_rerank_pool_factor", 3)
            ))
            HIERARCHY_BUNDLE_ON = bool(getattr(
                self.config.recall, "hierarchy_bundle_enabled", False
            ))
            HIERARCHY_BUNDLE_MAX_MEMBERS = max(2, int(getattr(
                self.config.recall, "hierarchy_bundle_max_members", 8
            )))
            HIERARCHY_BUNDLE_TOKEN_BUDGET = max(1, int(getattr(
                self.config.recall, "hierarchy_bundle_token_budget", 400
            )))
            HIERARCHY_BUNDLE_TOPIC_LIMIT = max(1, int(getattr(
                self.config.recall, "hierarchy_bundle_topic_limit", 12
            )))
            HIERARCHY_BUNDLE_NODE_BEAM = max(1, int(getattr(
                self.config.recall, "hierarchy_bundle_node_beam", 4
            )))
            HIERARCHY_BUNDLE_MEMBER_POOL = max(2, int(getattr(
                self.config.recall, "hierarchy_bundle_member_pool", 48
            )))
            HIERARCHY_BUNDLE_ALLOWED_NODE_TYPES = tuple(
                value.strip().casefold()
                for value in str(getattr(
                    self.config.recall,
                    "hierarchy_bundle_allowed_node_types",
                    "",
                ) or "").split(",")
                if value.strip()
            )
            HIERARCHY_BUNDLE_NOVEL_FLOOR_MARGIN = getattr(
                self.config.recall,
                "hierarchy_bundle_novel_floor_margin",
                None,
            )
            MULTI_SEED_BUNDLE_ON = bool(getattr(
                self.config.recall, "multi_seed_bundle_enabled", False
            ))
            MULTI_SEED_BUNDLE_MODE = str(getattr(
                self.config.recall, "multi_seed_bundle_mode", "episode_single"
            ) or "episode_single").strip().casefold()
            MULTI_SEED_BUNDLE_ALPHA = float(getattr(
                self.config.recall, "multi_seed_bundle_alpha", 0.6
            ))
            MULTI_SEED_BUNDLE_BETA = float(getattr(
                self.config.recall, "multi_seed_bundle_beta", 0.4
            ))
            MULTI_SEED_BUNDLE_GAMMA = float(getattr(
                self.config.recall, "multi_seed_bundle_gamma", 0.4
            ))
            MULTI_SEED_BUNDLE_MEMBER_FLOOR = float(getattr(
                self.config.recall, "multi_seed_bundle_member_floor", 0.20
            ))
            MULTI_SEED_BUNDLE_GROUP_FLOOR = float(getattr(
                self.config.recall, "multi_seed_bundle_group_floor", 0.35
            ))
            MULTI_SEED_BUNDLE_TAG_S1_FLOOR = float(getattr(
                self.config.recall, "multi_seed_bundle_tag_s1_floor", 0.0
            ))
            MULTI_SEED_BUNDLE_TAU = float(getattr(
                self.config.recall, "multi_seed_bundle_tau", 0.45
            ))
            MULTI_SEED_BUNDLE_LEXICAL_WEIGHT = float(getattr(
                self.config.recall, "multi_seed_bundle_lexical_weight", 0.35
            ))
            MULTI_SEED_BUNDLE_MAX_TAG_DF = max(1, int(getattr(
                self.config.recall, "multi_seed_bundle_max_tag_df", 8
            )))
            MULTI_SEED_BUNDLE_SCAN_LIMIT = max(1, int(getattr(
                self.config.recall, "multi_seed_bundle_scan_limit", 50
            )))
            MULTI_SEED_BUNDLE_MAX_MEMBERS = max(2, int(getattr(
                self.config.recall, "multi_seed_bundle_max_members", 8
            )))
            MULTI_SEED_BUNDLE_MAX_BUNDLES = max(0, int(getattr(
                self.config.recall, "multi_seed_bundle_max_bundles", 0
            )))
            MULTI_SEED_BUNDLE_MIN_EPISODE_SEEDS = max(1, int(getattr(
                self.config.recall, "multi_seed_bundle_min_episode_seeds", 1
            )))
            MULTI_SEED_BUNDLE_SINGLE_TAG_SEED_CEILING = float(getattr(
                self.config.recall,
                "multi_seed_bundle_single_tag_seed_ceiling", 1.01,
            ))
            MULTI_SEED_BUNDLE_TOKEN_BUDGET = max(1, int(getattr(
                self.config.recall, "multi_seed_bundle_token_budget", 400
            )))
            MULTI_SEED_TAG_MODE = MULTI_SEED_BUNDLE_MODE in {
                "tag_multi", "episode_tag",
            }
            RETRIEVAL_CLAUSES_ON = bool(getattr(
                self.config.recall, "retrieval_clauses_enabled", False
            ))
            RETRIEVAL_CLAUSE_MIN_SCORE = float(getattr(
                self.config.recall, "retrieval_clauses_min_score", 0.50
            ))
            RETRIEVAL_CLAUSE_MARGIN = float(getattr(
                self.config.recall, "retrieval_clauses_margin", 0.07
            ))
            RETRIEVAL_CLAUSE_CAP = max(0, int(getattr(
                self.config.recall, "retrieval_clauses_cap", 3
            )))
            RETRIEVAL_CLAUSE_REQUIRE_KEYWORD = bool(getattr(
                self.config.recall, "retrieval_clauses_require_keyword", True
            ))
            RETRIEVAL_CLAUSE_MIN_KEYWORD = float(getattr(
                self.config.recall, "retrieval_clauses_min_keyword_score", 0.10
            ))
            TOPIC_OVERLAY_ON = os.getenv(
                "MEMORY_TOPIC_OVERLAY_ENABLED", "false"
            ).strip().lower() in {"1", "true", "yes", "on"}
            TOPIC_GRAPH_ENTRY_ON = os.getenv(
                "MEMORY_TOPIC_GRAPH_ENTRY_ENABLED", "false"
            ).strip().lower() in {"1", "true", "yes", "on"}
            TOPIC_GRAPH_THRESHOLD = _float_env("MEMORY_TOPIC_GRAPH_THRESHOLD", 0.30)
            TOPIC_GRAPH_LIMIT = max(1, _int_env("MEMORY_TOPIC_GRAPH_LIMIT", 10))
            TOPIC_GRAPH_RELATION_TOLERANCE = _float_env(
                "MEMORY_TOPIC_GRAPH_RELATION_BGE_TOLERANCE", 1.0
            )
            SAME_SERIES_BUNDLE_ON = os.getenv(
                "MEMORY_SAME_SERIES_BUNDLE_ENABLED", "false"
            ).strip().lower() in {"1", "true", "yes", "on"}
            SAME_SERIES_BUNDLE_MAX_MEMBERS = max(
                1, _int_env("MEMORY_SAME_SERIES_BUNDLE_MAX_MEMBERS", 2)
            )
            SAME_SERIES_BUNDLE_FLOOR_MARGIN = _float_env(
                "MEMORY_SAME_SERIES_BUNDLE_FLOOR_MARGIN", -1.0
            )
            TOPIC_BUNDLE_ON = os.getenv(
                "MEMORY_TOPIC_BUNDLE_ENABLED", "false"
            ).strip().lower() in {"1", "true", "yes", "on"}
            TOPIC_BUNDLE_MAX_MEMBERS = max(
                1, _int_env("MEMORY_TOPIC_BUNDLE_MAX_MEMBERS", 2)
            )
            TOPIC_BUNDLE_FLOOR_MARGIN = _float_env(
                "MEMORY_TOPIC_BUNDLE_FLOOR_MARGIN", 0.0
            )
            EPISODE_BUNDLE_ON = os.getenv(
                "MEMORY_EPISODE_BUNDLE_ENABLED", "false"
            ).strip().lower() in {"1", "true", "yes", "on"}
            EPISODE_BUNDLE_MAX_MEMBERS = max(
                1, _int_env("MEMORY_EPISODE_BUNDLE_MAX_MEMBERS", 2)
            )
            EPISODE_BUNDLE_FLOOR_MARGIN = _float_env(
                "MEMORY_EPISODE_BUNDLE_FLOOR_MARGIN", 0.0
            )
            MIN_SCORE = _resolve_normal_min_score(request.min_score)

            final_limit = request.limit if request.limit > 0 else 10
            vdb_sem_limit = max(final_limit * 3, 30)
            vdb_kw_limit = max(final_limit * 5, 80)
            graph_limit = max(
                final_limit * 2,
                (request.schema_limit * 2) if request.schema_limit > 0 else 0,
                20,
            )

            # Log request
            await trace_log.log_request(
                query=request.query,
                limit=request.limit,
                layers=list(request.layers) if request.layers else None,
                min_score=MIN_SCORE,
                schema_min_score=request.schema_min_score,
                schema_limit=request.schema_limit,
                user_ids=list(request.user_ids or []),
                agent_ids=list(request.agent_ids or []),
                session_ids=list(request.session_ids or []),
            )

            # =========================================================
            # Stage 1: Embed query + Lemmatize query
            # =========================================================
            _t_embed = datetime.now()
            # Retrieval expansion widens embed/BM25 pool; NER/geo keep original.
            retrieval_query = expand_query_for_retrieval(request.query or "")
            query_embedding = request.query_embedding
            cache_hit = False
            embed_meta: Dict[str, Any] = {}
            if not query_embedding:
                query_embedding, embed_meta = await self.embed_service.embed_with_meta(
                    retrieval_query
                )
            else:
                cache_hit = True
                embed_meta = {"cache_hit": True, "source": "pre_computed"}

            if _query_capture is not None:
                _query_capture["vector"] = query_embedding

            query_lemmatized = lemmatize_for_bm25(retrieval_query)
            _embed_ms = (datetime.now() - _t_embed).total_seconds() * 1000

            await trace_log.log_embed_query(
                query=retrieval_query,
                dims=len(query_embedding),
                cache_hit=cache_hit,
                elapsed_ms=_embed_ms,
                provider_meta={
                    **embed_meta,
                    "retrieval_expanded": retrieval_query != (request.query or ""),
                    "original_query": request.query,
                },
            )

            # =========================================================
            # Stage 2: Build isolation params
            # =========================================================
            isolation_params = self._build_isolation_params(request)
            if isolation_params.get("error_msg"):
                response.error_code = 400
                response.error_message = isolation_params["error_msg"]
                return response

            # =========================================================
            # Stage 3: Parallel multi-channel recall
            # =========================================================
            _t_recall = datetime.now()
            vector_store = await self._get_vector_store()

            schema_limit = request.schema_limit
            schema_min_score = request.schema_min_score
            explicit_state_on = os.getenv(
                "MEMORY_HYBRID_V2_EXPLICIT_STATE_PROFILE_ENABLED", "false"
            ).strip().lower() in {"1", "true", "yes", "on"}
            explicit_state_min_score = _float_env(
                "MEMORY_HYBRID_V2_EXPLICIT_STATE_MIN_SCORE", 0.25
            )
            explicit_state_reserve = _int_env(
                "MEMORY_HYBRID_V2_EXPLICIT_STATE_RESERVED", 1
            ) if explicit_state_on else 0
            promoted_fact_on = os.getenv(
                "MEMORY_HYBRID_V2_PROMOTED_FACT_PROFILE_ENABLED", "false"
            ).strip().lower() in {"1", "true", "yes", "on"}
            promoted_fact_min_score = _float_env(
                "MEMORY_HYBRID_V2_PROMOTED_FACT_MIN_SCORE", 0.25
            )
            promoted_fact_reserve = _int_env(
                "MEMORY_HYBRID_V2_PROMOTED_FACT_RESERVED", 1
            ) if promoted_fact_on else 0

            # Build common search params
            search_kwargs = {
                "isolation_key": isolation_params.get("isolation_key", ""),
                "isolation_keys": isolation_params.get("isolation_keys"),
                "user_ids": isolation_params.get("user_ids"),
                "agent_ids": isolation_params.get("agent_ids"),
                "include_sources": request.include_sources,
                "exclude_sources": request.exclude_sources,
            }

            # 定向召回：request.layers 指定时 normal 路只召回目标层级，否则无关层级
            # 会挤占 limit 名额、导致目标层级召回不足（client 层的兜底过滤只能筛掉
            # 多余的，补不回少召的）。此前本 reader 忽略 request.layers，是静默失效。
            #
            # profile / graph 路仍按固定层级召回：其配额
            # 独立于 normal，不存在互相挤占，多召回的部分由 client 层按 layers 筛掉。
            # 三路的返回结果按固定下标 unpack，不能因跳过某路而改变任务顺序。
            _normal_layers = (
                [MemoryLayer.from_string(layer) for layer in request.layers]
                if request.layers else _VDB_RECALL_LAYERS
            )
            if (
                getattr(self.config.recall, "dynamic_episode_enabled", False)
                or getattr(self.config.recall, "series_episode_enabled", False)
                or
                SESSION_EPISODE_CONTEXT_ON
                or EPISODE_CAPSULE_ON
                or EPISODE_MEMBER_EXPANSION_ON
            ) and not request.layers:
                # Dynamic L3 is never an ordinary answer candidate. Keep the
                # normal pool focused on L2/L4 whenever such episodes can exist,
                # even when all episode read features are disabled; otherwise L3
                # consumes top-N recall capacity and is filtered only afterwards,
                # silently starving the L2 control arm. Episode read features use
                # the dedicated L3 pool below or exact parent-id lookup.
                _normal_layers = [MemoryLayer.L2_FACT, MemoryLayer.L4_IDENTITY]

            # Determine user_id and agent_ids for keyword search
            kw_user_id = request.user_id or (request.user_ids[0] if request.user_ids else "")
            kw_agent_ids = request.agent_ids if request.agent_ids else (
                [request.agent_id] if request.agent_id else None
            )

            # Parallel recall tasks
            # 召回池纳入 SUPERSEDED（被取代的旧节点）+ only_latest=False：
            # 命中旧节点后由 expand_evolution_chains 从命中点双向展开整链；
            # 同链多节点命中会在 expand 阶段去重为一条（以链头为代表）。
            # SHADOW（逻辑删除）不纳入。隐藏 L0 上下文保持只 ACTIVE。
            _recall_status = [MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED]
            recall_tasks = [
                # Channel 1: VDB Semantic (L0/L2/L3/L4)
                vector_store.search(
                    query_embedding=query_embedding,
                    layers=_normal_layers,
                    limit=vdb_sem_limit,
                    status_filter=_recall_status,
                    only_latest=False,
                    created_after=request.created_after,
                    **search_kwargs,
                ),
                # Channel 2: VDB Keyword (BM25, L0/L2/L3/L4)
                self._keyword_search(
                    vector_store,
                    query=query_lemmatized,
                    top_k=vdb_kw_limit,
                    user_id=kw_user_id,
                    agent_ids=kw_agent_ids,
                    isolation_key=search_kwargs.get("isolation_key", ""),
                    isolation_keys=search_kwargs.get("isolation_keys"),
                    layers=_normal_layers,
                    status_filter=_recall_status,
                    only_latest=False,
                    include_sources=request.include_sources,
                    exclude_sources=request.exclude_sources,
                ),
                # Profile VDB recall：L0 是 user-level，只按 user_ids 搜索，不携带
                # agent/session isolation。历史 agent-level 多链在结果解包后折叠为一条。
                # 用固定索引解包，故无 user 范围时返回空协程而非跳过任务。
                self._recall_user_level_l0(
                    vector_store, request,
                ),
            ]

            clause_idx = None
            retrieval_clause_info: Dict[str, Any] = {
                "enabled": RETRIEVAL_CLAUSES_ON,
                "hits": 0,
                "admitted": 0,
                "parent_ids": [],
            }
            clause_search = getattr(vector_store, "search_retrieval_clauses", None)
            l2_allowed = MemoryLayer.L2_FACT in _normal_layers
            if (
                RETRIEVAL_CLAUSES_ON
                and RETRIEVAL_CLAUSE_CAP > 0
                and l2_allowed
                and kw_user_id
                and callable(clause_search)
                and not request.include_sources
                and not request.exclude_sources
            ):
                clause_idx = len(recall_tasks)
                recall_tasks.append(clause_search(
                    query_embedding=query_embedding,
                    user_id=kw_user_id,
                    agent_ids=kw_agent_ids,
                    top_k=max(RETRIEVAL_CLAUSE_CAP * 10, 20),
                    min_score=RETRIEVAL_CLAUSE_MIN_SCORE,
                ))

            episode_sem_idx = episode_kw_idx = None
            if (
                SESSION_EPISODE_CONTEXT_ON
                or (
                    EPISODE_MEMBER_EXPANSION_ON
                    and EPISODE_MEMBER_MODE in {"direct", "series_direct"}
                )
            ):
                # L3 may also contain legacy/topic summaries. Pull a bounded but
                # wide pool, then keep only summary_kind=session_episode before RRF.
                episode_pool_limit = max(SESSION_EPISODE_LIMIT * 8, 100)
                episode_sem_idx = len(recall_tasks)
                recall_tasks.append(vector_store.search(
                    query_embedding=query_embedding,
                    layers=[MemoryLayer.L3_SUMMARY],
                    limit=episode_pool_limit,
                    score_threshold=0.0,
                    status_filter=[MemoryStatus.ACTIVE],
                    only_latest=True,
                    created_after=request.created_after,
                    **search_kwargs,
                ))
                episode_kw_idx = len(recall_tasks)
                recall_tasks.append(self._keyword_search(
                    vector_store,
                    query=query_lemmatized,
                    top_k=episode_pool_limit,
                    user_id=kw_user_id,
                    agent_ids=kw_agent_ids,
                    isolation_key=search_kwargs.get("isolation_key", ""),
                    isolation_keys=search_kwargs.get("isolation_keys"),
                    layers=[MemoryLayer.L3_SUMMARY],
                    status_filter=[MemoryStatus.ACTIVE],
                    only_latest=True,
                ))

            # Channel 3: Graph 正路（only if graph_store available）
            has_graph = self._graph_store is not None
            schema_search_on = has_graph and schema_limit != 0
            graph_user_id = request.user_id or (request.user_ids[0] if request.user_ids else "")
            graph_idx = None
            if schema_search_on:
                # Graph search: use user_id prefix match (not exact isolation_key)
                # because isolation_key may have wrong agent/session when agent_ids list is used.
                # L6 属于 schema 路，阈值用 schema_min_score，而非 MIN_SCORE。
                graph_idx = len(recall_tasks)
                graph_recall_threshold = (
                    min(schema_min_score, explicit_state_min_score)
                    if explicit_state_on else schema_min_score
                )
                recall_tasks.append(
                    self._graph_store.vector_search(
                        query_embedding=query_embedding,
                        isolation_key="",
                        layers=["l6_schema"],
                        limit=graph_limit,
                        score_threshold=graph_recall_threshold,
                    user_id=graph_user_id,
                    include_sources=request.include_sources,
                    exclude_sources=request.exclude_sources,
                    )
                )

            tag_index_idx = None
            if (
                (TAG_INDEX_NOMINEE_ON or (MULTI_SEED_BUNDLE_ON and MULTI_SEED_TAG_MODE))
                and (TAG_INDEX_NOMINEE_LIMIT > 0 or MULTI_SEED_TAG_MODE)
                and kw_user_id
                and getattr(vector_store, "_supports_tag_index", False)
                and not request.include_sources
                and not request.exclude_sources
            ):
                tag_index_idx = len(recall_tasks)
                recall_tasks.append(vector_store.search_tag_embeddings(
                    kw_user_id,
                    query_embedding,
                    topk=TAG_INDEX_NOMINEE_TOPK,
                    min_score=TAG_INDEX_NOMINEE_MIN_SCORE,
                ))

            results = await asyncio.gather(*recall_tasks, return_exceptions=True)
            if _defer_semantic_gate:
                failure = next((r for r in results if isinstance(r, Exception)), None)
                if failure is not None:
                    raise RuntimeError("Underflow recall channel failed") from failure

            # Unpack results (handle exceptions gracefully)
            # 固定前 3 路: vdb_semantic, vdb_keyword, profile
            vdb_semantic_hits = results[0] if not isinstance(results[0], Exception) else []
            vdb_keyword_hits = results[1] if not isinstance(results[1], Exception) else []
            # Dynamic L3 capsules are parent context, not global competitors. They
            # enter the answer only through an already selected L2 fact, so merely
            # enabling their write path cannot change ordinary HY top-k retrieval.
            vdb_semantic_hits = [
                item for item in vdb_semantic_hits if not is_episode_container(item)
            ]
            vdb_keyword_hits = [
                item for item in vdb_keyword_hits if not is_episode_container(item)
            ]
            hidden_l0_hits = (
                collapse_user_level_l0_hits(results[2])
                if not isinstance(results[2], Exception) else []
            )

            retrieval_clause_hits = []
            if clause_idx is not None and not isinstance(results[clause_idx], Exception):
                retrieval_clause_hits = list(results[clause_idx] or [])
            elif clause_idx is not None:
                logger.debug(
                    "[%s] retrieval clause search failed: %s",
                    self.VERSION,
                    results[clause_idx],
                )

            graph_schema_hits = []
            if graph_idx is not None:
                graph_schema_hits = results[graph_idx] if not isinstance(results[graph_idx], Exception) else []
                graph_schema_hits = [
                    hit for hit in graph_schema_hits
                    if not is_internal_profile_chain_hit(hit)
                ]
                if isinstance(results[graph_idx], Exception):
                    logger.warning(
                        "[%s] graph schema search failed: %s",
                        self.VERSION, results[graph_idx],
                    )
                elif explicit_state_on:
                    graph_schema_hits = [
                        hit for hit in graph_schema_hits
                        if float(hit.get("score") or 0.0) >= schema_min_score
                        or (
                            _graph_custom(hit).get("schema_kind") == "explicit_state"
                            and float(hit.get("score") or 0.0) >= explicit_state_min_score
                        )
                    ]

            tag_index_hits: List[Dict[str, Any]] = []
            tag_index_candidates: List[Dict[str, Any]] = []
            tag_index_info: Dict[str, Any] = {
                "enabled": TAG_INDEX_NOMINEE_ON,
                "applied": False,
            }
            if tag_index_idx is not None:
                value = results[tag_index_idx]
                if isinstance(value, Exception):
                    tag_index_info["reason"] = type(value).__name__
                else:
                    tag_index_hits = list(value or [])
                    matched_tags = [
                        str(hit.get("tag") or "") for hit in tag_index_hits
                        if hit.get("tag")
                    ]
                    if matched_tags and TAG_INDEX_NOMINEE_ON:
                        try:
                            tag_index_candidates = await vector_store.search(
                                query_embedding=query_embedding,
                                user_id=kw_user_id,
                                agent_ids=kw_agent_ids,
                                layers=[MemoryLayer.L2_FACT],
                                limit=TAG_INDEX_NOMINEE_SEARCH_LIMIT,
                                score_threshold=0.0,
                                status_filter=[MemoryStatus.ACTIVE],
                                only_latest=True,
                                tags_match_any=matched_tags,
                            )
                        except Exception as exc:
                            tag_index_info["reason"] = type(exc).__name__

            session_episode_results: List[Dict[str, Any]] = []
            dynamic_episode_results: List[Dict[str, Any]] = []
            if (
                SESSION_EPISODE_CONTEXT_ON
                or (
                    EPISODE_MEMBER_EXPANSION_ON
                    and EPISODE_MEMBER_MODE in {"direct", "series_direct"}
                )
            ):
                episode_sem = results[episode_sem_idx] if not isinstance(results[episode_sem_idx], Exception) else []
                episode_kw = results[episode_kw_idx] if not isinstance(results[episode_kw_idx], Exception) else []
                if SESSION_EPISODE_CONTEXT_ON:
                    session_episode_results = rank_session_episode_hits(
                        episode_sem, episode_kw, limit=SESSION_EPISODE_LIMIT,
                    )
                graph_candidates = []
                if (
                    EPISODE_MEMBER_EXPANSION_ON
                    and EPISODE_MEMBER_MODE in {"direct", "series_direct"}
                ):
                    dynamic_episode_results = rank_dynamic_episode_hits(
                        episode_sem,
                        episode_kw,
                        limit=EPISODE_MEMBER_EPISODE_LIMIT,
                        semantic_min=EPISODE_MEMBER_EPISODE_MIN_SIM,
                        series_only=EPISODE_MEMBER_MODE == "series_direct",
                    )

            # Proactive 路：intention（L7）从 VDB 召回（与 graph 解耦，全模式可用），
            # 过期惰性转 L2_FACT。仅当 intention_limit > 0 时启用。
            graph_intention_hits: List[Dict[str, Any]] = []
            if request.intention_limit > 0:
                _t_intent = datetime.now()
                _intent_stats: Dict[str, Any] = {}
                graph_intention_hits = await recall_intentions(
                    vector_store,
                    query_embedding,
                    user_ids=isolation_params.get("user_ids"),
                    agent_ids=isolation_params.get("agent_ids"),
                    isolation_key=isolation_params.get("isolation_key") or "",
                    isolation_keys=isolation_params.get("isolation_keys"),
                    limit=request.intention_limit,
                    stats_out=_intent_stats,
                    include_sources=request.include_sources,
                    exclude_sources=request.exclude_sources,
                )
                await trace_log.log_intention(
                    stats=_intent_stats,
                    elapsed_ms=(datetime.now() - _t_intent).total_seconds() * 1000,
                )

            if isinstance(results[0], Exception):
                logger.warning("[%s] VDB semantic search failed: %s", self.VERSION, results[0])
            if isinstance(results[1], Exception):
                logger.warning("[%s] VDB keyword search failed: %s", self.VERSION, results[1])

            _recall_ms = (datetime.now() - _t_recall).total_seconds() * 1000

            # Trace: recall results
            await trace_log.log_recall_vec(
                pool_size=vdb_sem_limit,
                hits=vdb_semantic_hits,
                elapsed_ms=_recall_ms,
            )
            await trace_log.log_hidden_l0_context(
                hits=hidden_l0_hits,
            )

            # =========================================================
            # Stage 4: Semantic gate on VDB
            # =========================================================
            # Profile-promoted facts use a lower, separately configured semantic
            # admission gate. Keep the raw recall score before the normal 0.3 gate
            # removes the item and keyword fallback later rebuilds it with a tiny
            # synthetic semantic floor.
            _raw_semantic_by_id = {
                str(item.get("node_id") or ""): float(item.get("score") or 0.0)
                for item in vdb_semantic_hits if item.get("node_id")
            }
            _promoted_semantic_candidates = [
                {
                    "node_id": item["node_id"],
                    "node": item["node"],
                    "score": float(item.get("score") or 0.0),
                    "source": "profile_semantic_candidate",
                    "_semantic": float(item.get("score") or 0.0),
                    "_semantic_admission": float(item.get("score") or 0.0),
                }
                for item in vdb_semantic_hits if item.get("node_id") and item.get("node") is not None
            ]
            if not _defer_semantic_gate:
                vdb_semantic_hits = [r for r in vdb_semantic_hits if r["score"] >= MIN_SCORE]

            # =========================================================
            # Stage 5: BM25 score normalization
            # =========================================================
            # 后端的 keyword_search 分两类：
            #   - keyword_score_normalized=True（后端已返回 [0,1] 分数）：
            #     分已在 [0,1]，直接用，不能再过 normalize_bm25（那个 sigmoid 为
            #     "经典 BM25 原始分 0~20" 标定，会把 ~1 的分压成 ~0.03）。
            #   - False（经典 BM25 原始分）：用 sigmoid 归一化。
            kw_normalized = getattr(vector_store, "keyword_score_normalized", False)
            midpoint, steepness = get_bm25_params(request.query, query_lemmatized)
            keyword_scores: Dict[str, float] = {}
            for r in vdb_keyword_hits:
                nid = r["node_id"]
                raw = r["score"]
                if kw_normalized:
                    keyword_scores[nid] = max(0.0, min(float(raw), 1.0))
                else:
                    keyword_scores[nid] = normalize_bm25(raw, midpoint, steepness)

            # Clauses are admission-only aliases. Require independent lexical
            # support from the visible parent, materialize at most a tiny number
            # of parents, and never expose the alias itself in the response.
            if retrieval_clause_hits:
                retrieval_clause_info["hits"] = len(retrieval_clause_hits)
                admitted_count = 0
                existing_ids = {
                    str(item.get("node_id") or "") for item in vdb_semantic_hits
                }
                semantic_cutoff = min(
                    (float(item.get("score") or 0.0) for item in vdb_semantic_hits),
                    default=0.0,
                )
                keyword_parent_ids = (
                    {
                        parent_id for parent_id, score in keyword_scores.items()
                        if score >= RETRIEVAL_CLAUSE_MIN_KEYWORD
                    }
                    if RETRIEVAL_CLAUSE_REQUIRE_KEYWORD else None
                )
                admitted_aliases = select_retrieval_clause_hits(
                    retrieval_clause_hits,
                    existing_parent_ids=existing_ids,
                    semantic_cutoff=semantic_cutoff,
                    min_margin=RETRIEVAL_CLAUSE_MARGIN,
                    cap=max(RETRIEVAL_CLAUSE_CAP * 3, RETRIEVAL_CLAUSE_CAP),
                    eligible_parent_ids=keyword_parent_ids,
                )
                if admitted_aliases:
                    parents = await vector_store.get_by_ids([
                        str(alias["parent_id"]) for alias in admitted_aliases
                    ])
                    parent_by_id = {node.node_id: node for node in parents or []}
                    for alias in admitted_aliases:
                        parent_id = str(alias["parent_id"])
                        node = parent_by_id.get(parent_id)
                        if node is None or node.layer != MemoryLayer.L2_FACT:
                            continue
                        if node.status not in {
                            MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED,
                        }:
                            continue
                        parent_clauses = eligible_retrieval_clauses(
                            node,
                            min_words=int(getattr(
                                self.config.recall,
                                "retrieval_clauses_min_words",
                                70,
                            )),
                        )
                        clause_index = int(alias.get("clause_index") or 0)
                        clause_text = (
                            parent_clauses[clause_index]
                            if 0 <= clause_index < len(parent_clauses) else ""
                        )
                        if not clause_text:
                            continue
                        vdb_semantic_hits.append({
                            "node_id": parent_id,
                            "node": node,
                            "score": float(alias["score"]),
                            "_retrieval_clause": clause_text,
                            "_retrieval_clause_score": float(alias["score"]),
                        })
                        admitted_count += 1
                        retrieval_clause_info["parent_ids"].append(parent_id)
                        if admitted_count >= RETRIEVAL_CLAUSE_CAP:
                            break
                retrieval_clause_info["admitted"] = admitted_count
            response.extra["retrieval_clauses"] = retrieval_clause_info

            # =========================================================
            # Stage 6: VDB fusion（按条加成）
            #   final = sem
            #         + 𝟙[bm25>0] · W_BM25 · bm25
            #         + 𝟙[ent>0] · W_TAG · max(tag_cos, graph_entity_hit)
            # tag_cos = LLM tag 实体 embedding；graph_entity = 正文 NER MENTIONS
            # =========================================================
            # for_boost=True：排除 PERSON 等不适类型，避免对话人名冲垮 top-k
            query_ents = extract_entities(request.query or "", for_boost=True)
            # 批量 embed：query 实体 span + 候选实体 tags
            emb_texts: List[str] = []
            for span, _lab in query_ents:
                if span and span not in emb_texts:
                    emb_texts.append(span)

            tag_cos_by_id: Dict[str, float] = {}
            tag_pair_by_id: Dict[str, Any] = {}
            dynamic_entity_tag_info: Dict[str, Any] = {
                "enabled": DYNAMIC_ENTITY_TAG_EMBED_ON,
                "query_entity_count": len(query_ents),
                "embedded_text_count": 0,
                "embedded_vector_count": 0,
            }
            if query_ents:
                # 先收集候选节点上的实体 tags
                cand_nodes_for_tag: List[Any] = []
                for r in list(vdb_semantic_hits) + list(vdb_keyword_hits):
                    n = r.get("node")
                    if n is not None:
                        cand_nodes_for_tag.append(n)
                mem_ent_map: Dict[str, List[Tuple[str, str]]] = {}
                for n in cand_nodes_for_tag:
                    nid = getattr(n, "node_id", None)
                    if not nid or nid in mem_ent_map:
                        continue
                    tags = list(getattr(n, "tags", None) or [])
                    ners = list(getattr(n, "tag_ners", None) or [])
                    ents = entity_tag_pairs(tags, ners if ners else None)
                    mem_ent_map[nid] = ents
                    for t_text, _ in ents:
                        if t_text and t_text not in emb_texts:
                            emb_texts.append(t_text)

                emb_map: Dict[str, List[float]] = {}
                if emb_texts:
                    dynamic_entity_tag_info["embedded_text_count"] = len(emb_texts)
                    if DYNAMIC_ENTITY_TAG_EMBED_ON:
                        try:
                            vecs = await self.embed_service.embed_batch(emb_texts)
                            for t, v in zip(emb_texts, vecs or []):
                                if v:
                                    emb_map[t] = v
                            dynamic_entity_tag_info["embedded_vector_count"] = len(emb_map)
                        except Exception as e:
                            if _defer_semantic_gate:
                                raise
                            logger.debug("[%s] entity tag embed failed: %s", self.VERSION, e)

                if emb_map:
                    for nid, ments in mem_ent_map.items():
                        cos, pair = max_entity_tag_cos(
                            query_ents, ments, emb_map,
                            min_cos=TAG_COS_THR, require_same_group=True,
                        )
                        if cos > 0:
                            tag_cos_by_id[nid] = cos
                            tag_pair_by_id[nid] = pair

            # geo-intent prior：问句无 boostable 实体但仍在问地点时，
            # 给候选 GPE/LOC/FAC tag 一个 modest prior（+ w_tag·prior），
            # 并用问句月/年软约束，避免把蜜月/其他行程一起抬满。
            if GEO_INTENT_PRIOR > 0:
                cand_nodes_geo: List[Any] = []
                seen_geo = set()
                for r in list(vdb_semantic_hits) + list(vdb_keyword_hits):
                    n = r.get("node")
                    nid = r.get("node_id") or getattr(n, "node_id", None)
                    if n is None or not nid or nid in seen_geo:
                        continue
                    seen_geo.add(nid)
                    cand_nodes_geo.append(n)
                tag_cos_by_id = apply_geo_intent_prior(
                    request.query or "",
                    query_ents,
                    cand_nodes_geo,
                    tag_cos_by_id,
                    prior=GEO_INTENT_PRIOR,
                )

            # Graph Entity MENTIONS：正文实体反查（tags 没有 Tokyo 时仍可 boost）
            entity_boost_by_id: Dict[str, float] = {}
            entity_hit_meta: Dict[str, Any] = {}
            if (
                query_ents and has_graph and self._graph_store is not None
                and not request.include_sources and not request.exclude_sources
            ):
                try:
                    _uid = request.user_id or (
                        request.user_ids[0] if request.user_ids else ""
                    )
                    _aid = request.agent_id or (
                        (request.agent_ids[0] if request.agent_ids else None)
                        or "default_agent"
                    )
                    _scope = MemoryNode.build_topic_scope_key(_uid, _aid)
                    _ehits = await self._graph_store.find_memories_by_entities(
                        _scope, query_ents, limit=max(vdb_kw_limit, 100),
                    )
                    for _h in _ehits or []:
                        _nid = _h.get("node_id")
                        if not _nid:
                            continue
                        _sc = float(_h.get("match_score") or 1.0)
                        if _sc > entity_boost_by_id.get(_nid, 0.0):
                            entity_boost_by_id[_nid] = _sc
                            entity_hit_meta[_nid] = _h
                except Exception as e:
                    if _defer_semantic_gate:
                        raise
                    logger.debug("[%s] graph entity lookup failed: %s", self.VERSION, e)

            def _fused_ent(nid: str) -> float:
                return max(
                    tag_cos_by_id.get(nid, 0.0),
                    entity_boost_by_id.get(nid, 0.0),
                )

            has_bm25 = bool(keyword_scores)
            seen_vdb: Dict[str, int] = {}
            vdb_scored: List[Dict[str, Any]] = []

            # Process semantic hits first
            for r in vdb_semantic_hits:
                nid = r["node_id"]
                sem_score = r["score"]
                bm25_score = keyword_scores.get(nid, 0.0)
                fused = _fused_ent(nid)
                final = score_vdb_node_additive(
                    sem_score,
                    bm25_score=bm25_score,
                    tag_cos=fused,
                    w_bm25=W_BM25,
                    w_tag=W_TAG,
                )
                seen_vdb[nid] = len(vdb_scored)
                vdb_scored.append({
                    "node_id": nid,
                    "node": r["node"],
                    "score": final,
                    "source": "vdb",
                    "_semantic": sem_score,
                    "_bm25": bm25_score,
                    "_tag_cos": tag_cos_by_id.get(nid, 0.0),
                    "_entity_boost": entity_boost_by_id.get(nid, 0.0),
                    "_tag_pair": tag_pair_by_id.get(nid),
                    "_entity_hit": entity_hit_meta.get(nid),
                    "_retrieval_clause": r.get("_retrieval_clause"),
                    "_retrieval_clause_score": r.get("_retrieval_clause_score"),
                })

            # Add keyword-only hits (not found by semantic)
            for r in vdb_keyword_hits:
                nid = r["node_id"]
                if nid not in seen_vdb:
                    bm25_score = keyword_scores.get(nid, 0.0)
                    fused = _fused_ent(nid)
                    # 虚 sem 底：纯 keyword 命中否则 final≈w_bm25·bm25 常 < MIN_SCORE
                    _kw_sem = float(KW_ONLY_SEM_FLOOR) * float(bm25_score)
                    final = score_vdb_node_additive(
                        _kw_sem,
                        bm25_score=bm25_score,
                        tag_cos=fused,
                        w_bm25=W_BM25,
                        w_tag=W_TAG,
                    )
                    if final > 0:
                        seen_vdb[nid] = len(vdb_scored)
                        vdb_scored.append({
                            "node_id": nid,
                            "node": r["node"],
                            "score": final,
                            "source": "vdb_keyword_only",
                            "_semantic": _kw_sem,
                            "_semantic_admission": _raw_semantic_by_id.get(nid, 0.0),
                            "_bm25": bm25_score,
                            "_tag_cos": tag_cos_by_id.get(nid, 0.0),
                            "_entity_boost": entity_boost_by_id.get(nid, 0.0),
                            "_tag_pair": tag_pair_by_id.get(nid),
                            "_entity_hit": entity_hit_meta.get(nid),
                        })

            # Entity 图命中但不在 sem/kw 池：从 VDB 补进（sem=0 + entity boost）
            _missing_ent = [
                nid for nid in entity_boost_by_id if nid not in seen_vdb
            ]
            if _missing_ent:
                try:
                    _nodes = await vector_store.get_by_ids(_missing_ent)
                    _by = {
                        n.node_id: n for n in (_nodes or []) if n is not None
                    }
                    for nid in _missing_ent:
                        node = _by.get(nid)
                        if node is None:
                            continue
                        if getattr(node, "status", None) is not None and \
                                getattr(node.status, "value", "") == MemoryStatus.SHADOW.value:
                            continue
                        fused = _fused_ent(nid)
                        final = score_vdb_node_additive(
                            0.0,
                            bm25_score=0.0,
                            tag_cos=fused,
                            w_bm25=W_BM25,
                            w_tag=W_TAG,
                        )
                        if final <= 0:
                            continue
                        seen_vdb[nid] = len(vdb_scored)
                        vdb_scored.append({
                            "node_id": nid,
                            "node": node,
                            "score": final,
                            "source": "graph_entity",
                            "_semantic": 0.0,
                            "_bm25": 0.0,
                            "_tag_cos": 0.0,
                            "_entity_boost": entity_boost_by_id.get(nid, 0.0),
                            "_tag_pair": None,
                            "_entity_hit": entity_hit_meta.get(nid),
                        })
                except Exception as e:
                    logger.debug("[%s] materialize entity hits failed: %s", self.VERSION, e)

            # Memory Strength（默认关闭）：normal 通道 idle 衰减 × 频次（profile 层
            # L0/L4 不参与），在 sort 前乘进 score。
            if getattr(self.config.recall, "strength_enabled", False):
                _tau = getattr(self.config.recall, "strength_tau", 180.0)
                _hidden_l0_vals = {layer.value for layer in _HIDDEN_L0_LAYERS}
                apply_strength_to_normal(
                    vdb_scored, profile_layers=_hidden_l0_vals,
                    score_key="score", tau=_tau,
                )

            # P1：先 RRF blend，再做日级 temporal admit ——
            # temporal_day 无 sem/bm25，若放在 RRF 之后会被 blend 压掉。
            if RRF_BLEND > 0 and len(vdb_scored) > 1:
                vdb_scored = blend_rrf_into_scores(
                    vdb_scored, blend=RRF_BLEND, bm25_weight=1.25,
                )
                # rebuild index after list rewrite
                seen_vdb = {
                    it["node_id"]: i for i, it in enumerate(vdb_scored)
                    if it.get("node_id")
                }

            # ── Light temporal（正则抽日/月，无 LLM / 无 rewrite）──
            # 日级：list_by_user 扫入同日记忆（解决 "9th December 2023" 语义池 miss）。
            # 月级：仅对已在池内的候选软加分（避免整月灌池）。
            _day_hints = query_day_hints(request.query or "")
            _month_hints = query_time_hints(request.query or "")
            if _day_hints and TEMPORAL_ADMIT > 0:
                try:
                    _t_uid = request.user_id or (
                        request.user_ids[0] if request.user_ids else ""
                    )
                    _t_aid = request.agent_id or (
                        (request.agent_ids[0] if request.agent_ids else None)
                        or None
                    )
                    if _t_uid:
                        _day_nodes = await vector_store.list_by_user(
                            user_id=_t_uid,
                            agent_id=_t_aid,
                            limit=TEMPORAL_LIST_LIMIT,
                            status_filter=_recall_status,
                            layers=_normal_layers,
                            only_latest=False,
                            with_vectors=False,
                            include_sources=request.include_sources,
                            exclude_sources=request.exclude_sources,
                        )
                        _anchor = max(
                            (float(x.get("score") or 0.0) for x in vdb_scored),
                            default=float(TEMPORAL_ADMIT),
                        )
                        # 同日记忆要能挤进 top-k：对齐当前池顶附近，而不是固定低 prior。
                        _admit_score = max(
                            float(TEMPORAL_ADMIT), float(_anchor) - 0.05
                        )
                        _admitted = 0
                        for _n in _day_nodes or []:
                            if _admitted >= TEMPORAL_ADMIT_CAP:
                                break
                            _nid = getattr(_n, "node_id", None)
                            if not _nid:
                                continue
                            if getattr(_n, "status", None) is not None and \
                                    getattr(_n.status, "value", "") == MemoryStatus.SHADOW.value:
                                continue
                            if not memory_matches_day_hints(
                                getattr(_n, "memory_at", None), _day_hints
                            ):
                                continue
                            if _nid in seen_vdb:
                                _ix = seen_vdb[_nid]
                                vdb_scored[_ix]["score"] = float(
                                    vdb_scored[_ix].get("score") or 0.0
                                ) + TEMPORAL_BOOST
                                vdb_scored[_ix]["_temporal_boost"] = TEMPORAL_BOOST
                            else:
                                seen_vdb[_nid] = len(vdb_scored)
                                vdb_scored.append({
                                    "node_id": _nid,
                                    "node": _n,
                                    "score": float(_admit_score),
                                    "source": "temporal_day",
                                    "_semantic": float(_admit_score),
                                    "_bm25": 0.0,
                                    "_tag_cos": 0.0,
                                    "_entity_boost": 0.0,
                                    "_temporal_admit": _admit_score,
                                })
                                _admitted += 1
                except Exception as e:
                    logger.debug("[%s] temporal day admit failed: %s", self.VERSION, e)
            elif _month_hints and TEMPORAL_MONTH_BOOST > 0:
                for _it in vdb_scored:
                    _nd = _it.get("node")
                    _content = (
                        getattr(_nd, "content", None) if _nd is not None
                        else _it.get("content")
                    ) or ""
                    _ma = getattr(_nd, "memory_at", None) if _nd is not None else None
                    if memory_matches_time_hints(
                        _content, _month_hints, memory_at=_ma
                    ):
                        _it["score"] = float(_it.get("score") or 0.0) + TEMPORAL_MONTH_BOOST
                        _it["_temporal_month_boost"] = TEMPORAL_MONTH_BOOST

            # Sort VDB by final score
            vdb_scored.sort(key=lambda x: x["score"], reverse=True)

            # Trace: BM25/keyword fusion
            await trace_log.log_bm25(
                pool_size=len(vdb_semantic_hits),
                query_terms=query_lemmatized.split() if query_lemmatized else [],
                hits=vdb_scored[:20],
                raw_hits=vdb_keyword_hits,
                bm25_scores=keyword_scores,
                normalize_method="passthrough" if kw_normalized else "sigmoid",
                sigmoid_midpoint=None if kw_normalized else midpoint,
                sigmoid_steepness=None if kw_normalized else steepness,
                has_bm25=has_bm25,
                elapsed_ms=0,
            )

            # =========================================================
            # Stage 7: Profile 正路 —— graph L6 evidence boost
            # =========================================================
            # 正路：graph vector_search 命中的 L6（已过 schema_min_score），按
            # 证据数加成排序。每项 score = raw 语义分；_internal 仅用于正路内部排序。
            forward_l6: List[Dict[str, Any]] = []
            if schema_search_on and graph_schema_hits:
                for r in graph_schema_hits:
                    custom = _graph_custom(r)
                    ev_count = 0
                    try:
                        ev_refs = await self._graph_store.get_evidence_vdbrefs(r["node_id"])
                        ev_count = len(ev_refs) if ev_refs else 0
                    except Exception:
                        pass
                    ev_boost = compute_evidence_boost(ev_count, EV_SATURATE, EV_BOOST_MAX)
                    internal_score = r["score"] * (1.0 + ev_boost)
                    forward_l6.append({
                        "node_id": r["node_id"],
                        "node": None,
                        "content": r.get("content", ""),
                        "score": r["score"],
                        "retrieval_source": "schema_forward",
                        "memory_source": r.get("memory_source") or "default",
                        "layer": r.get("layer", "l6_schema"),
                        "confidence": r.get("confidence", r["score"]),
                        "_internal": internal_score,
                        "_evidence_count": ev_count,
                        "_schema_kind": custom.get("schema_kind", "behavior_pattern"),
                        "_schema_semantic": float(r.get("score") or 0.0),
                    })
                forward_l6.sort(key=lambda x: x["_internal"], reverse=True)

            # =========================================================
            # Stage 8: Schema 反路 + RRF 融合
            # =========================================================
            # 反路：从 normal 命中的 VDB 节点反查支撑它们的 L6（按支撑度排序，不卡阈值）。
            reverse_l6: List[Dict[str, Any]] = []
            if schema_search_on and vdb_scored:
                reverse_l6 = await reverse_lookup_l6(
                    self._graph_store,
                    [v["node_id"] for v in vdb_scored],
                    limit=graph_limit,
                    include_sources=request.include_sources,
                    exclude_sources=request.exclude_sources,
                )
                reverse_l6 = [
                    hit for hit in reverse_l6
                    if hit.get("_schema_kind") != "balanced_persona_catalog"
                ]

            # 正反 RRF 融合（rank 级，规避正路语义分 vs 反路支撑度不可比）。
            # RRF 输出只保留 node_id/node，content/layer 需从 node_id→item map 重新 hydrate。
            fused_l6: List[Dict[str, Any]] = []
            if forward_l6 or reverse_l6:
                _l6_by_id: Dict[str, Dict[str, Any]] = {}
                for it in forward_l6 + reverse_l6:
                    _l6_by_id.setdefault(it["node_id"], it)  # 正路优先保留（先填）
                fused = rrf_fuse({"forward": forward_l6, "reverse": reverse_l6})
                for f in fused:
                    src = _l6_by_id.get(f["node_id"], {})
                    fused_l6.append({
                        "node_id": f["node_id"],
                        "node": None,
                        "content": src.get("content", ""),
                        "layer": src.get("layer", "l6_schema"),
                        "score": float(f.get("rrf_score", 0.0)),
                        "retrieval_source": "schema_l6",
                        "memory_source": src.get("memory_source") or "default",
                        "confidence": src.get("confidence"),
                        "_schema_kind": src.get("_schema_kind", "behavior_pattern"),
                        "_schema_semantic": src.get("_schema_semantic"),
                    })

            # =========================================================
            # Stage 9: Intention（不占 limit 配额，末尾追加）
            # =========================================================
            intention_results = list(graph_intention_hits)

            # =========================================================
            # Stage 10: 演化链展开（normal VDB + 隐藏 L0 上下文；均有 node 对象）
            # =========================================================
            _t_evo = datetime.now()
            expandable = vdb_scored + list(hidden_l0_hits)
            if expandable:
                expanded = await expand_evolution_chains(
                    vector_store, expandable,
                    include_sources=request.include_sources,
                    exclude_sources=request.exclude_sources,
                )
            else:
                expanded = []
            # 时间过滤末端兜底：keyword_search(BM25) / graph 通道不支持下推 created_after，
            # 统一在这里按事件时间再过一遍（见 _retrieval/temporal.py）
            expanded = filter_hits_by_created_after(expanded, request.created_after)
            _evo_ms = (datetime.now() - _t_evo).total_seconds() * 1000
            await trace_log.log_evolution(
                input_size=len(expandable),
                evolved_count=sum(1 for r in expanded if r.get("is_evolved")),
                elapsed_ms=_evo_ms,
            )

            # =========================================================
            # Stage 11: 组装 —— normal（截断 + 出口阈值）+ L6 schema
            # =========================================================
            _hidden_l0_layer_vals = {layer.value for layer in _HIDDEN_L0_LAYERS}

            def _layer_of(item: Dict[str, Any]) -> str:
                nd = item.get("node")
                return nd.layer.value if (nd and getattr(nd, "layer", None)) else (item.get("layer") or "")

            # normal：演化展开后的 VDB 项（排除隐藏 L0）
            normal_results = [
                it for it in expanded
                if _layer_of(it) not in _hidden_l0_layer_vals
            ]
            # ── Stage 11a: 记忆关系图重排（用法 A/B/C，见 docs/pro_graph_design.md）──
            # **必须在截断之前**：A 的价值在于"从几十条扩池候选里重新决定哪 N 条进最终结果"，
            # 只把已选出的 top-N 换个顺序收益很小。
            relation_paths: List[Dict[str, Any]] = []
            _graph_stats: Dict[str, Any] = {}
            if self._expansion_on():
                _t_graph = datetime.now()
                _pool_before = len(normal_results)
                normal_results, relation_paths, _graph_stats = await self._apply_graph_rerank(
                    hits=normal_results,
                    vector_store=vector_store,
                    query_embedding=query_embedding,
                    min_score=MIN_SCORE,
                    request=request,
                    dynamic_entity_tag_embed_enabled=DYNAMIC_ENTITY_TAG_EMBED_ON,
                    strict=_defer_semantic_gate,
                )
                dynamic_entity_tag_info["graph_embedded_text_count"] = int(
                    _graph_stats.get("dynamic_entity_tag_text_count") or 0
                )
                dynamic_entity_tag_info["graph_embedded_vector_count"] = int(
                    _graph_stats.get("dynamic_entity_tag_vector_count") or 0
                )
                if _graph_stats:
                    logger.debug("[%s] graph rerank: %s", self.VERSION, _graph_stats)
                await trace_log.log_graph_rerank(
                    stats=_graph_stats,
                    pool_size=_pool_before,
                    admitted_ids=_graph_stats.get("admitted_ids") or [
                        it.get("node_id") for it in normal_results
                        if it.get("source") == "graph_relaxed"
                        or it.get("graph_merged")
                    ],
                    elapsed_ms=(datetime.now() - _t_graph).total_seconds() * 1000,
                )

            normal_results = _filter_normal_results_by_score(
                normal_results, MIN_SCORE,
            )
            SESSION_EPISODE_CONTEXT_APPLIED = bool(session_episode_results)
            normal_results.sort(key=lambda x: x.get("score", 0), reverse=True)
            if SESSION_EPISODE_CONTEXT_APPLIED:
                # Reserve one normal slot for the compact fact bundle. With the
                # default limit=10 this yields 5 episodes + 1 bundle; with a small
                # caller limit it still preserves both layers.
                _episode_slots = min(
                    SESSION_EPISODE_LIMIT,
                    final_limit if final_limit <= 1 else final_limit - 1,
                )
                _fact_slots = SESSION_EPISODE_FACT_LIMIT if final_limit > 1 else 0
                normal_results = select_episode_fact_context(
                    session_episode_results + normal_results,
                    episode_limit=_episode_slots,
                    fact_limit=_fact_slots,
                )[:final_limit]
            response.extra["session_episode_context_applied"] = SESSION_EPISODE_CONTEXT_APPLIED
            # P0 light diversity：社交模板 / 池 DF / 近重复降权 + MMR
            if DIVERSITY_ON and normal_results and not SESSION_EPISODE_CONTEXT_APPLIED:
                if SOCIAL_BOILER_PENALTY > 0:
                    normal_results = social_boilerplate_demote(
                        normal_results, penalty=SOCIAL_BOILER_PENALTY,
                    )
                if POOL_DF_THR > 0 and POOL_DF_PENALTY > 0:
                    normal_results = pool_df_demote(
                        normal_results,
                        df_thr=POOL_DF_THR,
                        penalty=POOL_DF_PENALTY,
                    )
                if NEAR_DUP_THR > 0 and NEAR_DUP_PENALTY > 0:
                    normal_results = near_dup_demote(
                        normal_results,
                        jaccard_thr=NEAR_DUP_THR,
                        penalty=NEAR_DUP_PENALTY,
                    )
                pre_mmr_normal_results = list(normal_results)
                _pre_rerank_limit = (
                    max(final_limit, final_limit * (
                        2 if MULTI_SEED_BUNDLE_ON else LOCAL_RERANK_POOL_FACTOR
                    ))
                    if LOCAL_RERANK_ON else final_limit
                )
                if 0.0 < MMR_LAMBDA < 0.999 and len(normal_results) > _pre_rerank_limit:
                    normal_results = mmr_select(
                        normal_results,
                        _pre_rerank_limit,
                        lambda_=MMR_LAMBDA,
                    )
                else:
                    normal_results = normal_results[:_pre_rerank_limit]
            elif not SESSION_EPISODE_CONTEXT_APPLIED:
                pre_mmr_normal_results = list(normal_results)
                _pre_rerank_limit = (
                    max(final_limit, final_limit * (
                        2 if MULTI_SEED_BUNDLE_ON else LOCAL_RERANK_POOL_FACTOR
                    ))
                    if LOCAL_RERANK_ON else final_limit
                )
                normal_results = normal_results[:_pre_rerank_limit]
            original_mmr_pool = list(normal_results)
            lexical_guard_info: Dict[str, Any] = {"enabled": False, "applied": False}
            if (
                os.getenv("MEMORY_MMR_LEXICAL_SURVIVAL_ENABLED", "false").lower()
                in {"1", "true", "yes", "on"}
                and LOCAL_RERANK_ON
                and not SESSION_EPISODE_CONTEXT_APPLIED
            ):
                normal_results, lexical_guard_info = nominate_lexical_after_mmr(
                    pre_mmr_normal_results,
                    normal_results,
                    limit=_pre_rerank_limit,
                )
            response.extra["mmr_lexical_guard"] = lexical_guard_info

            episode_member_info: Dict[str, Any] = {
                "enabled": EPISODE_MEMBER_EXPANSION_ON,
                "mode": EPISODE_MEMBER_MODE,
                "episodes": len(dynamic_episode_results),
                "member_ids": [],
                "added": 0,
            }
            if (
                EPISODE_MEMBER_EXPANSION_ON
                and EPISODE_MEMBER_MODE in {"direct", "series_direct", "anchored"}
                and not SESSION_EPISODE_CONTEXT_APPLIED
                and LOCAL_RERANK_ON
                and not (
                    EPISODE_MEMBER_MODE == "anchored"
                    and (request.include_sources or request.exclude_sources)
                )
            ):
                _pool_limit = (
                    max(final_limit, final_limit * LOCAL_RERANK_POOL_FACTOR)
                    if LOCAL_RERANK_ON else final_limit
                )
                if EPISODE_MEMBER_MODE in {"direct", "series_direct"}:
                    episode_members, episode_member_info = (
                        await self._dynamic_episode_member_candidates(
                            vector_store,
                            dynamic_episode_results,
                            query_embedding,
                            existing_ids={
                                str(item.get("node_id") or "")
                                for item in normal_results if item.get("node_id")
                            },
                            total_limit=EPISODE_MEMBER_LIMIT,
                            per_episode=EPISODE_MEMBER_PER_EPISODE,
                            min_similarity=EPISODE_MEMBER_MIN_SIM,
                        )
                    )
                else:
                    episode_members, episode_member_info = (
                        await self._anchored_episode_member_candidates(
                        vector_store,
                        normal_results,
                        query_embedding,
                        existing_ids={
                            str(item.get("node_id") or "")
                            for item in normal_results if item.get("node_id")
                        },
                        episode_limit=EPISODE_MEMBER_EPISODE_LIMIT,
                        total_limit=EPISODE_MEMBER_LIMIT,
                        per_episode=EPISODE_MEMBER_PER_EPISODE,
                        seed_min_similarity=EPISODE_MEMBER_EPISODE_MIN_SIM,
                        member_min_similarity=EPISODE_MEMBER_MIN_SIM,
                        )
                    )
                episode_member_info["enabled"] = True
                episode_member_info["mode"] = EPISODE_MEMBER_MODE
                episode_member_info["pool_limit"] = _pool_limit
                if (EPISODE_MEMBER_MODE == "series_direct"
                        and os.getenv("MEMORY_SERIES_TAIL_GUARD_ENABLED", "false").lower() == "true"
                        and os.getenv("MEMORY_EPISODE_MEMBER_POOL_REPLACE_ENABLED", "true").lower() == "true"):
                    from ._retrieval.series_tail_guard import guarded_series_pool
                    # Compare stored root vectors on one scale; never compare
                    # fused normal scores against raw member cosine.
                    guard_ids = sorted({item["node_id"] for item in normal_results + episode_members})
                    guard_error = None
                    try:
                        guard_similarities = await self._candidate_similarities(
                            vector_store, guard_ids, query_embedding,
                        ) if episode_members else {}
                    except Exception as exc:
                        guard_similarities = {}
                        guard_error = type(exc).__name__
                    normal_results, guard_info = guarded_series_pool(
                        normal_results, episode_members, guard_similarities, pool_limit=_pool_limit,
                    )
                    if guard_error:
                        guard_info["vector_read_error"] = guard_error
                    episode_member_info["tail_guard"] = {"enabled": True, "policy": "series_tail_cosine_v1", **guard_info}
                else:
                    normal_results = merge_episode_members_into_pool(
                        normal_results, episode_members, pool_limit=_pool_limit,
                    )
            response.extra["episode_member_expansion"] = episode_member_info

            episode_capsule_info: Dict[str, Any] = {}
            if (
                EPISODE_CAPSULE_ON
                and not EPISODE_MEMBER_EXPANSION_ON
                and not SESSION_EPISODE_CONTEXT_APPLIED
            ):
                parent_ids = []
                for item in normal_results[:max(1, EPISODE_CAPSULE_FIRST_RANK_MAX)]:
                    node = item.get("node")
                    if node is None or getattr(node, "layer", None) != MemoryLayer.L2_FACT:
                        continue
                    for parent_id in parent_episode_ids(item):
                        if parent_id not in parent_ids:
                            parent_ids.append(parent_id)
                if parent_ids:
                    parent_nodes = await vector_store.get_by_ids(parent_ids)
                    parent_items = {
                        node.node_id: {
                            "node_id": node.node_id,
                            "node": node,
                            "score": 0.0,
                            "source": "episode_capsule_parent",
                        }
                        for node in (parent_nodes or []) if node is not None
                    }
                    parent_sims = await self._candidate_similarities(
                        vector_store, list(parent_items), query_embedding,
                    )
                    normal_results, episode_capsule_info = select_episode_capsule_context(
                        normal_results,
                        parent_items,
                        parent_sims,
                        query_min=EPISODE_CAPSULE_QUERY_MIN,
                        assignment_min=EPISODE_CAPSULE_ASSIGNMENT_MIN,
                        first_rank_max=EPISODE_CAPSULE_FIRST_RANK_MAX,
                        member_limit=EPISODE_CAPSULE_MEMBER_LIMIT,
                        render_mode=EPISODE_CAPSULE_RENDER_MODE,
                    )
            response.extra["episode_capsule"] = episode_capsule_info

            local_rerank_info: Dict[str, Any] = {
                "enabled": LOCAL_RERANK_ON,
                "applied": False,
                "pool_size": len(normal_results),
            }
            if LOCAL_RERANK_ON and not SESSION_EPISODE_CONTEXT_APPLIED:
                baseline_bge_pool = list(normal_results)
                # A4 composes with the frozen baseline by consuming the exact
                # pool after all legacy pre-BGE guards (MMR, lexical survival,
                # and bounded episode-member replacement).  When planning does
                # not admit a bundle, the legacy rerank/post-BGE path below is
                # left byte-for-byte intact.
                multi_seed_pool = (
                    canonical_seed_view(baseline_bge_pool)
                    if MULTI_SEED_BUNDLE_ON else []
                )
                if multi_seed_pool:
                    multi_seed_pool.sort(
                        key=lambda item: float(item.get("score") or 0.0),
                        reverse=True,
                    )
                    if (
                        0.0 < MMR_LAMBDA < 0.999
                        and len(multi_seed_pool) > _pre_rerank_limit
                    ):
                        multi_seed_pool = mmr_select(
                            multi_seed_pool,
                            _pre_rerank_limit,
                            lambda_=MMR_LAMBDA,
                        )
                    else:
                        multi_seed_pool = multi_seed_pool[:_pre_rerank_limit]
                rerank_text_fn = build_rerank_text
                if os.getenv("MEMORY_RERANK_DATE_VIEW_ENABLED", "false").lower() == "true":
                    from ._retrieval.date_view import normalize_rerank_dates, date_view_applies
                    if date_view_applies(request.query or "", os.getenv("MEMORY_RERANK_DATE_VIEW_MODE", "all")):
                        def rerank_text_fn(item):
                            return normalize_rerank_dates(build_rerank_text(item))
                if os.getenv("MEMORY_RERANK_RECORD_DATE_ENABLED", "false").lower() == "true":
                    from ._retrieval.record_date_view import record_date_text
                    base_text_fn = rerank_text_fn

                    def rerank_text_fn(item):
                        return record_date_text(item, base_text_fn(item))

                    local_rerank_info["record_date_view"] = {"enabled": True, "policy": "record_date_root_v1"}
                reranker = get_local_reranker(
                    getattr(
                        self.config.recall,
                        "local_rerank_model",
                        "nreimers/mmarco-mMiniLMv2-L6-H384-v1",
                    ),
                    device=getattr(self.config.recall, "local_rerank_device", "auto"),
                    max_length=getattr(self.config.recall, "local_rerank_max_length", 512),
                    batch_size=getattr(self.config.recall, "local_rerank_batch_size", 16),
                )
                multi_seed_info: Dict[str, Any] = {
                    "enabled": MULTI_SEED_BUNDLE_ON, "applied": False,
                    "mode": MULTI_SEED_BUNDLE_MODE,
                }
                if MULTI_SEED_BUNDLE_ON:
                    if not (
                        has_graph and self._graph_store is not None
                        and request.user_ids and len(request.user_ids) == 1
                    ):
                        multi_seed_info["reason"] = "unsupported_scope_or_graph"
                    else:
                        from ._retrieval.profile_owner_gate import owner_name_from_l0
                        multi_seed_owner = owner_name_from_l0(hidden_l0_hits)
                        multi_seed_subjects = extract_topic_query_subjects(
                            request.query or "", multi_seed_owner,
                        )
                        graph_agent = (
                            request.agent_ids[0]
                            if request.agent_ids and len(request.agent_ids) == 1
                            else next((
                                str(getattr(item.get("node"), "agent_id", "") or "")
                                for item in baseline_bge_pool
                                if getattr(item.get("node"), "agent_id", None)
                            ), request.agent_id or "default_agent")
                        )
                        hierarchy_scope = MemoryNode.build_topic_scope_key(
                            request.user_ids[0], graph_agent,
                        )
                        try:
                            normal_results, multi_seed_info = (
                                await build_multi_seed_bundle_pool(
                                    graph_store=self._graph_store,
                                    vector_store=vector_store,
                                    isolation_key=hierarchy_scope,
                                    query=request.query or "",
                                    query_embedding=query_embedding,
                                    seed_pool=multi_seed_pool,
                                    fallback_pool=baseline_bge_pool,
                                    mode=MULTI_SEED_BUNDLE_MODE,
                                    tag_hits=tag_index_hits,
                                    excluded_tags=multi_seed_subjects,
                                    alpha=MULTI_SEED_BUNDLE_ALPHA,
                                    beta=MULTI_SEED_BUNDLE_BETA,
                                    gamma=MULTI_SEED_BUNDLE_GAMMA,
                                    member_floor=MULTI_SEED_BUNDLE_MEMBER_FLOOR,
                                    group_floor=MULTI_SEED_BUNDLE_GROUP_FLOOR,
                                    tag_s1_floor=MULTI_SEED_BUNDLE_TAG_S1_FLOOR,
                                    tau=MULTI_SEED_BUNDLE_TAU,
                                    lexical_weight=MULTI_SEED_BUNDLE_LEXICAL_WEIGHT,
                                    max_tag_df=MULTI_SEED_BUNDLE_MAX_TAG_DF,
                                    scan_limit=MULTI_SEED_BUNDLE_SCAN_LIMIT,
                                    max_members=MULTI_SEED_BUNDLE_MAX_MEMBERS,
                                    max_bundles=MULTI_SEED_BUNDLE_MAX_BUNDLES,
                                    min_episode_seeds=MULTI_SEED_BUNDLE_MIN_EPISODE_SEEDS,
                                    single_tag_seed_ceiling=(
                                        MULTI_SEED_BUNDLE_SINGLE_TAG_SEED_CEILING
                                    ),
                                    token_budget=MULTI_SEED_BUNDLE_TOKEN_BUDGET,
                                    expected_seed_count=2 * final_limit,
                                    min_units=final_limit,
                                )
                            )
                        except Exception as exc:
                            normal_results = baseline_bge_pool
                            multi_seed_info = {
                                "enabled": True, "applied": False,
                                "mode": MULTI_SEED_BUNDLE_MODE,
                                "reason": "planner_failed",
                                "error_type": type(exc).__name__,
                            }
                response.extra["multi_seed_bundle"] = multi_seed_info
                # Hierarchy is a final-stage additive candidate.  It must not
                # change the pre-existing pipeline's top-K merely by widening
                # the rerank output.  Keep the legacy limit here and retain the
                # separately returned ``selected_pool`` for hierarchy backfill.
                _rerank_limit = (
                    len(normal_results) if SAME_SERIES_TIMELINE_ON else final_limit
                )
                _downstream_candidate_guards = bool(
                    lexical_guard_info.get("applied")
                    or os.getenv(
                        "MEMORY_POST_BGE_FUSION_RESERVE_ENABLED", "false"
                    ).lower() in {"1", "true", "yes", "on"}
                    or TOPIC_OVERLAY_ON
                    or TOPIC_GRAPH_ENTRY_ON
                    or HIERARCHY_BUNDLE_ON
                )
                if multi_seed_info.get("applied"):
                    normal_results, rank_info, selected_pool, scores_by_id = (
                        await to_thread_guarded(
                        rerank_items_with_scores,
                        request.query or "",
                        normal_results,
                        limit=_rerank_limit,
                        text_fn=rerank_text_fn,
                        scorer=reranker.score,
                    ))
                elif _downstream_candidate_guards:
                    _survival_original_pool = (
                        original_mmr_pool
                        if lexical_guard_info.get("applied") else normal_results
                    )
                    normal_results, rank_info, selected_pool, scores_by_id = await to_thread_guarded(
                        rerank_with_lexical_survival,
                        request.query or "",
                        _survival_original_pool,
                        normal_results,
                        lexical_guard_info.get("nominated"),
                        limit=_rerank_limit,
                        text_fn=rerank_text_fn,
                        scorer=reranker.score,
                    )
                else:
                    normal_results, rank_info = await to_thread_guarded(
                        rerank_items,
                        request.query or "",
                        normal_results,
                        limit=_rerank_limit,
                        text_fn=rerank_text_fn,
                        scorer=reranker.score,
                    )
                    selected_pool, scores_by_id = [], {}
                if (
                    not multi_seed_info.get("applied")
                    and TAG_INDEX_NOMINEE_ON and tag_index_hits
                    and tag_index_candidates
                ):
                    owner_name = None
                    try:
                        from ._retrieval.profile_owner_gate import owner_name_from_l0
                        owner_name = owner_name_from_l0(hidden_l0_hits)
                    except Exception:
                        pass
                    excluded_tags = extract_topic_query_subjects(
                        request.query or "", owner_name,
                    )
                    baseline_pool = list(selected_pool or baseline_bge_pool)
                    nominees, tag_index_info = nominate_multi_tag_candidates(
                        tag_index_hits,
                        tag_index_candidates,
                        excluded_tags=excluded_tags,
                        existing_ids=[item.get("node_id") for item in baseline_pool],
                        min_matches=TAG_INDEX_NOMINEE_MIN_MATCHES,
                        limit=TAG_INDEX_NOMINEE_LIMIT,
                        weight=TAG_INDEX_NOMINEE_WEIGHT,
                        max_tag_df=TAG_INDEX_NOMINEE_MAX_TAG_DF,
                    )
                    tag_index_info["candidate_count"] = len(tag_index_candidates)
                    tag_index_info["excluded_tags"] = list(excluded_tags)
                    if nominees:
                        nominee = nominees[0]
                        nominee_id = str(nominee.get("node_id") or "")
                        guarded_pool = list(baseline_pool)
                        displaced = None
                        if len(guarded_pool) >= _pre_rerank_limit:
                            displaced = guarded_pool[-1].get("node_id")
                            guarded_pool = [*guarded_pool[:-1], nominee]
                        else:
                            guarded_pool.append(nominee)
                        tagged_results, tag_rank_info, tag_selected_pool, tag_scores = (
                            await to_thread_guarded(
                                rerank_with_guarded_nominee,
                                request.query or "",
                                normal_results,
                                baseline_pool,
                                scores_by_id,
                                guarded_pool,
                                nominee_id,
                                limit=_rerank_limit,
                                text_fn=rerank_text_fn,
                                scorer=reranker.score,
                            )
                        )
                        survived = bool(
                            (tag_rank_info.get("lexical_survival") or {}).get(
                                "nominee_in_guarded_top_k"
                            )
                        )
                        tag_index_info.update({
                            "applied": survived,
                            "nominated": nominee_id,
                            "displaced": displaced,
                            "survived_bge_top_k": survived,
                            "selected_pool": (
                                tag_rank_info.get("lexical_survival") or {}
                            ).get("selected_pool"),
                        })
                        if survived:
                            normal_results = tagged_results
                            selected_pool = tag_selected_pool
                            scores_by_id = tag_scores
                            rank_info = tag_rank_info
                response.extra["tag_index_nominee"] = tag_index_info
                if (
                    rank_info.get("applied")
                    and os.getenv("MEMORY_POST_BGE_FUSION_RESERVE_ENABLED", "false").lower()
                    in {"1", "true", "yes", "on"}
                    and selected_pool
                ):
                    normal_results, post_bge_info = apply_post_bge_fusion_reserve(
                        request.query or "", selected_pool, normal_results, scores_by_id,
                        limit=_rerank_limit,
                    )
                    response.extra["post_bge_fusion_reserve"] = post_bge_info
                    rank_info["returned"] = len(normal_results)
                    rank_info["top_scores"] = [
                        round(float(item.get("_local_rerank_score") or 0.0), 6)
                        for item in normal_results
                    ]
                owner_name = None
                if (
                    not multi_seed_info.get("applied")
                    and (TOPIC_OVERLAY_ON or TOPIC_GRAPH_ENTRY_ON or HIERARCHY_BUNDLE_ON)
                ):
                    from ._retrieval.profile_owner_gate import owner_name_from_l0
                    owner_name = owner_name_from_l0([
                        item for item in expanded
                        if _layer_of(item) == MemoryLayer.L0_BASIC_INFO.value
                    ])
                if (
                    not multi_seed_info.get("applied")
                    and (TOPIC_OVERLAY_ON or TOPIC_GRAPH_ENTRY_ON)
                ):
                    topic_subjects = extract_topic_query_subjects(
                        request.query or "", owner_name,
                    )
                if (
                    not multi_seed_info.get("applied")
                    and rank_info.get("applied") and TOPIC_OVERLAY_ON
                ):
                    overlay = topic_overlay_candidates(
                        request.query or "", pre_mmr_normal_results, normal_results,
                        owner_name=owner_name,
                    )
                    missing = [
                        value["item"] for value in overlay
                        if str(value["item"].get("node_id") or "") not in scores_by_id
                    ]
                    if missing:
                        missing_scores = await to_thread_guarded(
                            reranker.score,
                            request.query or "",
                            [rerank_text_fn(item) for item in missing],
                        )
                        if missing_scores is not None and len(missing_scores) == len(missing):
                            scores_by_id.update({
                                str(item.get("node_id") or ""): float(score)
                                for item, score in zip(missing, missing_scores)
                            })
                    slots = max(1, round(final_limit * (
                        0.2 if is_collection_query(request.query or "") else 0.1
                    )))
                    normal_results, topic_overlay_info = apply_topic_overlay(
                        normal_results, overlay, scores_by_id,
                        limit=_rerank_limit, slots=slots,
                    )
                    response.extra["topic_overlay"] = topic_overlay_info
                if (
                    not multi_seed_info.get("applied")
                    and
                    rank_info.get("applied") and TOPIC_GRAPH_ENTRY_ON
                    and has_graph and self._graph_store is not None
                    and request.user_ids and len(request.user_ids) == 1
                    and not request.include_sources and not request.exclude_sources
                ):
                    graph_agent = (
                        request.agent_ids[0] if request.agent_ids and len(request.agent_ids) == 1
                        else next((
                            str(getattr(item.get("node"), "agent_id", "") or "")
                            for item in pre_mmr_normal_results
                            if getattr(item.get("node"), "agent_id", None)
                        ), request.agent_id or "default_agent")
                    )
                    graph_candidates, graph_probe = await topic_graph_candidates(
                        self._graph_store, vector_store,
                        query=request.query or "", query_embedding=query_embedding,
                        user_id=request.user_ids[0], agent_id=graph_agent,
                        selected=normal_results, owner_name=owner_name,
                        threshold=TOPIC_GRAPH_THRESHOLD,
                        topic_limit=TOPIC_GRAPH_LIMIT,
                    )
                    graph_items = [value["item"] for value in graph_candidates]
                    graph_scores = await to_thread_guarded(
                        reranker.score,
                        request.query or "",
                        [rerank_text_fn(item) for item in graph_items],
                    ) if graph_items else []
                    if graph_scores is not None and len(graph_scores) == len(graph_items):
                        scores_by_id.update({
                            str(item.get("node_id") or ""): float(score)
                            for item, score in zip(graph_items, graph_scores)
                        })
                        graph_slots = max(1, round(final_limit * (
                            0.2 if is_collection_query(request.query or "") else 0.1
                        )))
                        normal_results, graph_info = apply_topic_graph_candidates(
                            request.query or "", topic_subjects, normal_results,
                            graph_candidates, scores_by_id,
                            limit=_rerank_limit, slots=graph_slots,
                            relation_bge_tolerance=TOPIC_GRAPH_RELATION_TOLERANCE,
                        )
                    else:
                        graph_info = {"enabled": True, "applied": False,
                                      "reason": "scorer_unavailable_or_invalid"}
                    response.extra["topic_graph_probe"] = graph_probe
                    response.extra["topic_graph_entry"] = graph_info
                if rank_info.get("applied") and not multi_seed_info.get("applied"):
                    normal_results, final_unique_info = ensure_unique_top_k(
                        normal_results,
                        [
                            *selected_pool,
                            *pre_mmr_normal_results,
                        ],
                        scores_by_id,
                        limit=_rerank_limit,
                    )
                    if final_unique_info.get("applied"):
                        response.extra["final_normal_dedup"] = final_unique_info
                    if (
                        SAME_SERIES_BUNDLE_ON and has_graph
                        and self._graph_store is not None
                    ):
                        series_candidates, series_probe = await same_series_bundle_candidates(
                            self._graph_store, vector_store, normal_results,
                        )
                        series_items = list(series_candidates)
                        series_scores = await to_thread_guarded(
                            reranker.score,
                            request.query or "",
                            [rerank_text_fn(item) for item in series_items],
                        ) if series_items else []
                        if series_scores is not None and len(series_scores) == len(series_items):
                            scores_by_id.update({
                                str(item.get("node_id") or ""): float(score)
                                for item, score in zip(series_items, series_scores)
                            })
                            normal_results, series_bundle_info = select_same_series_bundle(
                                request.query or "", normal_results, series_items,
                                scores_by_id,
                                max_members=SAME_SERIES_BUNDLE_MAX_MEMBERS,
                                floor_margin=SAME_SERIES_BUNDLE_FLOOR_MARGIN,
                            )
                        else:
                            series_bundle_info = {
                                "enabled": True, "applied": False,
                                "reason": "scorer_unavailable_or_invalid",
                            }
                        response.extra["same_series_bundle_probe"] = series_probe
                        response.extra["same_series_bundle"] = series_bundle_info
                    if (
                        EPISODE_BUNDLE_ON
                        and not request.include_sources
                        and not request.exclude_sources
                    ):
                        episode_bundle_candidates, episode_bundle_probe = (
                            await self._anchored_episode_member_candidates(
                                vector_store, normal_results, query_embedding,
                                existing_ids={
                                    str(item.get("node_id") or "")
                                    for item in normal_results if item.get("node_id")
                                },
                                episode_limit=final_limit,
                                total_limit=max(final_limit, final_limit * 2),
                                per_episode=max(2, EPISODE_BUNDLE_MAX_MEMBERS),
                                seed_min_similarity=EPISODE_MEMBER_EPISODE_MIN_SIM,
                                member_min_similarity=EPISODE_MEMBER_MIN_SIM,
                            )
                        )
                        episode_items = list(episode_bundle_candidates)
                        episode_scores = await to_thread_guarded(
                            reranker.score,
                            request.query or "",
                            [rerank_text_fn(item) for item in episode_items],
                        ) if episode_items else []
                        if episode_scores is not None and len(episode_scores) == len(episode_items):
                            scores_by_id.update({
                                str(item.get("node_id") or ""): float(score)
                                for item, score in zip(episode_items, episode_scores)
                            })
                            normal_results, episode_bundle_info = select_episode_bundle(
                                request.query or "", normal_results, episode_items,
                                scores_by_id,
                                max_members=EPISODE_BUNDLE_MAX_MEMBERS,
                                floor_margin=EPISODE_BUNDLE_FLOOR_MARGIN,
                            )
                        else:
                            episode_bundle_info = {
                                "enabled": True, "applied": False,
                                "reason": "scorer_unavailable_or_invalid",
                            }
                        response.extra["episode_bundle_probe"] = episode_bundle_probe
                        response.extra["episode_bundle"] = episode_bundle_info
                    if TOPIC_BUNDLE_ON and graph_candidates:
                        normal_results, topic_bundle_info = select_topic_bundle(
                            request.query or "", normal_results, graph_candidates,
                            scores_by_id,
                            max_members=TOPIC_BUNDLE_MAX_MEMBERS,
                            floor_margin=TOPIC_BUNDLE_FLOOR_MARGIN,
                        )
                        response.extra["topic_bundle"] = topic_bundle_info
                    if (
                        HIERARCHY_BUNDLE_ON and has_graph
                        and self._graph_store is not None
                        and request.user_ids and len(request.user_ids) == 1
                        and not request.include_sources and not request.exclude_sources
                    ):
                        hierarchy_info: Dict[str, Any] = {
                            "enabled": True, "applied": False,
                        }
                        hierarchy_fallback = list(normal_results[:final_limit])
                        try:
                            graph_agent = (
                                request.agent_ids[0]
                                if request.agent_ids and len(request.agent_ids) == 1
                                else next((
                                    str(getattr(item.get("node"), "agent_id", "") or "")
                                    for item in pre_mmr_normal_results
                                    if getattr(item.get("node"), "agent_id", None)
                                ), request.agent_id or "default_agent")
                            )
                            hierarchy_scope = MemoryNode.build_topic_scope_key(
                                request.user_ids[0], graph_agent,
                            )
                            hierarchy_bundle, hierarchy_info = (
                                await build_hierarchy_bundle_candidate(
                                    graph_store=self._graph_store,
                                    vector_store=vector_store,
                                    isolation_key=hierarchy_scope,
                                    query=request.query or "",
                                    query_embedding=query_embedding,
                                    anchor_ids=[
                                        str(item.get("node_id") or "")
                                        for item in selected_pool
                                        if item.get("node_id")
                                    ],
                                    required_anchor_ids=[
                                        str(item.get("node_id") or "")
                                        for item in hierarchy_fallback
                                        if item.get("node_id")
                                    ],
                                    owner_name=owner_name or "",
                                    scorer=reranker.score,
                                    text_fn=rerank_text_fn,
                                    run_scorer=to_thread_guarded,
                                    topic_limit=HIERARCHY_BUNDLE_TOPIC_LIMIT,
                                    node_beam_limit=HIERARCHY_BUNDLE_NODE_BEAM,
                                    member_pool=HIERARCHY_BUNDLE_MEMBER_POOL,
                                    max_members=HIERARCHY_BUNDLE_MAX_MEMBERS,
                                    token_budget=HIERARCHY_BUNDLE_TOKEN_BUDGET,
                                    allowed_node_types=(
                                        HIERARCHY_BUNDLE_ALLOWED_NODE_TYPES
                                    ),
                                )
                            )
                            bundle_score = (
                                hierarchy_bundle.get("_hierarchy_bundle_score")
                                if hierarchy_bundle is not None else None
                            )
                            ranked_pool = sorted(
                                (
                                    {
                                        **item,
                                        "_local_rerank_score": float(scores_by_id.get(
                                            str(item.get("node_id") or ""),
                                            item.get("_local_rerank_score", float("-inf")),
                                        )),
                                    }
                                    for item in selected_pool
                                    if str(item.get("node_id") or "") in scores_by_id
                                ),
                                key=lambda item: (
                                    -float(item.get("_local_rerank_score") or float("-inf")),
                                    str(item.get("node_id") or ""),
                                ),
                            )
                            if hierarchy_bundle is None:
                                normal_results = hierarchy_fallback
                            else:
                                normal_results, competition_info = compete_hierarchy_bundle(
                                    ranked_pool, hierarchy_bundle, bundle_score,
                                    limit=final_limit, baseline=hierarchy_fallback,
                                    novel_floor_margin=(
                                        HIERARCHY_BUNDLE_NOVEL_FLOOR_MARGIN
                                    ),
                                )
                                hierarchy_info.update(competition_info)
                        except Exception as exc:
                            normal_results = hierarchy_fallback
                            hierarchy_info = {
                                "enabled": True,
                                "applied": False,
                                "reason": "hierarchy_bundle_failed",
                                "error_type": type(exc).__name__,
                            }
                        response.extra["hierarchy_bundle"] = hierarchy_info
                    elif HIERARCHY_BUNDLE_ON:
                        normal_results = normal_results[:final_limit]
                        response.extra["hierarchy_bundle"] = {
                            "enabled": True,
                            "applied": False,
                            "reason": "unsupported_scope_or_graph",
                        }
                    rank_info["returned"] = len(normal_results)
                    rank_info["top_scores"] = [
                        round(float(item.get("_local_rerank_score") or 0.0), 6)
                        for item in normal_results
                    ]
                local_rerank_info.update(rank_info)
                local_rerank_info.update({
                    "model": reranker.model_name,
                    "device": reranker.device,
                    "load_error": reranker.load_error,
                })
                if MULTI_SEED_BUNDLE_ON and not rank_info.get("applied"):
                    normal_results = baseline_bge_pool[:final_limit]
                    response.extra["multi_seed_bundle"] = {
                        **multi_seed_info,
                        "applied": False,
                        "reason": "reranker_unavailable",
                    }
                if HIERARCHY_BUNDLE_ON and not rank_info.get("applied"):
                    normal_results = normal_results[:final_limit]
                    response.extra["hierarchy_bundle"] = {
                        "enabled": True,
                        "applied": False,
                        "reason": "reranker_unavailable",
                    }
            elif not SESSION_EPISODE_CONTEXT_APPLIED:
                normal_results = normal_results[:final_limit]
            if (
                SAME_SERIES_TIMELINE_ON
                and not SESSION_EPISODE_CONTEXT_APPLIED
                and not (response.extra.get("multi_seed_bundle") or {}).get("applied")
            ):
                from ._graph.rerank import merge_same_series_in_pool
                normal_results = merge_same_series_in_pool(normal_results)[:final_limit]
                if local_rerank_info.get("applied"):
                    local_rerank_info["returned"] = len(normal_results)
            if (
                EPISODE_MEMBER_EXPANSION_ON
                and EPISODE_MEMBER_MODE == "anchored_attach"
                and not SESSION_EPISODE_CONTEXT_APPLIED
                and LOCAL_RERANK_ON
                and not (response.extra.get("multi_seed_bundle") or {}).get("applied")
                and not request.include_sources
                and not request.exclude_sources
            ):
                episode_members, episode_member_info = (
                    await self._anchored_episode_member_candidates(
                        vector_store,
                        normal_results,
                        query_embedding,
                        existing_ids={
                            str(item.get("node_id") or "")
                            for item in normal_results if item.get("node_id")
                        },
                        episode_limit=EPISODE_MEMBER_EPISODE_LIMIT,
                        total_limit=EPISODE_MEMBER_LIMIT,
                        per_episode=EPISODE_MEMBER_PER_EPISODE,
                        seed_min_similarity=EPISODE_MEMBER_EPISODE_MIN_SIM,
                        member_min_similarity=EPISODE_MEMBER_MIN_SIM,
                    )
                )
                from ._graph.rerank import merge_expansions_into_seeds
                attached = [
                    {
                        **member,
                        "graph_via": member.get("_episode_anchor_id"),
                        "graph_relation": "episode_co_occurs",
                    }
                    for member in episode_members
                    if member.get("_episode_anchor_id")
                ]
                normal_results = merge_expansions_into_seeds(
                    normal_results, attached,
                )
                episode_member_info["enabled"] = True
                episode_member_info["attached"] = len(attached)
                episode_member_info["top_ids_unchanged"] = True
            response.extra["episode_member_expansion"] = episode_member_info
            response.extra["local_rerank"] = local_rerank_info

            if getattr(
                self.config.recall, "supplemental_propositions_enabled", False,
            ):
                from ._retrieval.profile_owner_gate import owner_name_from_l0
                owner_name = owner_name_from_l0([
                    item for item in expanded
                    if _layer_of(item) == MemoryLayer.L0_BASIC_INFO.value
                ])
                normal_results, supplement_info = apply_semantic_single_supplement(
                    request.query or "", normal_results,
                    owner_name=owner_name or "",
                )
                response.extra["supplemental_propositions"] = supplement_info

            # Search 的 schema 通道只公开 L6。L0 仍作为 owner gate 的隐藏上下文，
            # 由 get_user_profile() 单独返回；promoted L2 回到 normal，不占 schema 配额。
            normal_ids = {
                str(member.get("node_id") or "")
                for item in normal_results
                for member in (item.get("_bundle_items") or [item])
                if member.get("node_id")
            }
            _promoted_candidate_ids = {
                item.get("node_id") for item in vdb_scored if item.get("node_id")
            }
            promoted_candidate_pool = list(vdb_scored) + [
                item for item in _promoted_semantic_candidates
                if item.get("node_id") not in _promoted_candidate_ids
            ]
            promoted_normal_facts = select_promoted_normal_facts(
                promoted_candidate_pool,
                min_score=promoted_fact_min_score,
                reserve=promoted_fact_reserve,
                excluded_node_ids=normal_ids,
            ) if promoted_fact_on else []
            l0_results = [
                it for it in expanded
                if _layer_of(it) in _hidden_l0_layer_vals
            ]
            l0_results.sort(key=lambda x: x.get("score", 0), reverse=True)
            if os.getenv(
                "MEMORY_PROFILE_OWNER_NAME_GATE_ENABLED", "false"
            ).strip().lower() in {"1", "true", "yes", "on"}:
                fused_l6, owner_gate_info = gate_l6_schemas(
                    request.query or "", l0_results, fused_l6,
                )
                response.extra["schema_owner_gate"] = owner_gate_info
            if promoted_normal_facts:
                reserved = promoted_normal_facts[:final_limit]
                normal_results = (
                    normal_results[:max(0, final_limit - len(reserved))]
                    + reserved
                )
            schema_results = [
                {**item, "_schema_channel": True}
                for item in select_schema_results(
                    fused_l6,
                    schema_limit=schema_limit,
                    explicit_state_reserve=explicit_state_reserve,
                )
            ]
            await trace_log.log_merge_schema(
                fused_size=len(vdb_scored),
                schema_size=len(fused_l6),
                merged_size=len(normal_results) + len(schema_results),
                final_limit=final_limit,
            )

            # 最终结果：normal + L6 schema + intention。L0 不再进入 Search。
            final_results = normal_results + schema_results
            if intention_results:
                final_results = final_results + intention_results

            # =========================================================
            # Stage 12: Build response
            # =========================================================
            for item in final_results:
                node: Optional[MemoryNode] = item.get("node")
                node_id = item.get("node_id", "")
                is_evolved = bool(item.get("is_evolved"))

                if node:
                    # VDB node；图扩展并进 seed 后 item["content"] 为合成文本
                    content = item.get("content") or node.content
                    layer = node.layer.value
                    speculate = getattr(node, "speculate", None)
                    source_raw_memory_id = getattr(node, "source_raw_memory_id", None)
                    tags = list(node.tags) if getattr(node, "tags", None) else []
                    memory_at = int(node.memory_at.timestamp()) if node.memory_at else None
                    gmt_created = int(node.gmt_created.timestamp()) if node.gmt_created else None
                else:
                    # Graph node
                    content = item.get("content", "")
                    layer = item.get("layer", "l6_schema")
                    speculate = None
                    source_raw_memory_id = None
                    tags = []
                    memory_at = None
                    gmt_created = None

                display_score = item.get("score", 0.0)

                # Every final result is self-contained for answer consumers.
                # Episode capsules already have per-member times; evolved memories
                # expand the full chain; plain/profile/graph items get one When.
                content = format_final_content({**item, "content": content})
                catalog_suffix = str(item.get("_balanced_catalog_suffix") or "").strip()
                if catalog_suffix:
                    content = content.rstrip() + "\n\n" + catalog_suffix
                supplement_suffix = str(
                    item.get("_specific_supplement_suffix") or ""
                ).strip()
                if supplement_suffix:
                    content = content.rstrip() + "\n\n" + supplement_suffix

                mem_entry = {
                    "memory_id": node_id,
                    "content": content,
                    "layer": layer,
                    "score": float(display_score),
                    "user_id": (getattr(node, "user_id", "") if node else "") or "",
                    "agent_id": (getattr(node, "agent_id", "") if node else "") or "",
                    "access_count": getattr(node, "access_count", 0) if node else 0,
                    "owner": getattr(node, "owner", None) if node else None,
                    "speculate": speculate,
                    "source_raw_memory_id": source_raw_memory_id,
                    "tags": tags,
                    "memory_at": memory_at,
                    "gmt_created": gmt_created,
                    "source": (
                        (getattr(node, "source", "default") if node else None)
                        or item.get("memory_source") or "default"
                    ),
                    "retrieval_source": (
                        item.get("retrieval_source") or item.get("source") or ""
                    ),
                    "_schema_channel": bool(item.get("_schema_channel")),
                    # 调用方自定义 meta（透传负载）。只透出 custom["meta"]，custom 里
                    # 其余是内部字段（request_id / supersede_reason 等），不外泄。
                    "meta": list(((getattr(node, "custom", None) or {}) if node else {}).get("meta") or []),
                    # Private pipeline marker consumed by HyMemoryClient before
                    # returning the public response.  Canonical states have their
                    # own exact-key lifecycle and must not enter fuzzy dedup.
                    "_memory_kind": (
                        ((getattr(node, "custom", None) or {}).get("memory_kind") or "")
                        if node else ""
                    ),
                }
                if item.get("_local_rerank_score") is not None:
                    mem_entry["rerank_score"] = float(item["_local_rerank_score"])
                if item.get("_capsule_memories") is not None:
                    mem_entry["capsule_memories"] = list(item["_capsule_memories"])
                    mem_entry["episode_id"] = item.get("node_id") or ""
                    mem_entry["member_ids"] = list(item.get("_fact_ids") or [])
                    mem_entry["timeline"] = list(item.get("_episode_timeline") or [])
                if item.get("_timeline_member_ids") is not None:
                    mem_entry["member_ids"] = list(item["_timeline_member_ids"])
                    mem_entry["timeline"] = list(item.get("_timeline") or [])
                elif item.get("_bundle_items") is not None:
                    mem_entry["member_ids"] = [
                        str(member.get("node_id") or "")
                        for member in item["_bundle_items"]
                        if member.get("node_id")
                    ]
                if is_evolved:
                    mem_entry["evolution_chain"] = item.get("evolution_chain", [])

                response.memories.append(mem_entry)

            # 防泄漏兜底：主过滤必须已在 VDB / Graph 的 limit 前完成；这里只验证
            # 图扩展、演化链等组合步骤没有把被排除来源重新带回来。
            from ..utils.source_filter import source_allowed
            response.memories = [
                memory for memory in response.memories
                if source_allowed(
                    memory.get("source"),
                    request.include_sources,
                    request.exclude_sources,
                )
            ]
            response.total_found = len(response.memories)
            response.extra["reader"] = self.VERSION
            dynamic_entity_tag_info["total_embedded_text_count"] = int(
                dynamic_entity_tag_info.get("embedded_text_count") or 0
            ) + int(dynamic_entity_tag_info.get("graph_embedded_text_count") or 0)
            dynamic_entity_tag_info["total_embedded_vector_count"] = int(
                dynamic_entity_tag_info.get("embedded_vector_count") or 0
            ) + int(dynamic_entity_tag_info.get("graph_embedded_vector_count") or 0)
            response.extra["dynamic_entity_tag_embedding"] = dynamic_entity_tag_info
            response.extra["channels"] = {
                "vdb_semantic": len(vdb_semantic_hits),
                "vdb_keyword": len(vdb_keyword_hits),
                "schema_forward_l6": len(forward_l6),
                "schema_reverse_l6": len(reverse_l6),
                "schema_fused_l6": len(fused_l6),
                "graph_intention": len(graph_intention_hits),
                "hidden_l0_context": len(hidden_l0_hits),
            }
            response.extra["config"] = {
                "w_bm25": W_BM25,
                "w_tag": W_TAG,
                "tag_cos_thr": TAG_COS_THR,
                "dynamic_entity_tag_embed_enabled": DYNAMIC_ENTITY_TAG_EMBED_ON,
                "tag_index_nominee_enabled": TAG_INDEX_NOMINEE_ON,
                "ev_boost_max": EV_BOOST_MAX,
                "ev_saturate": EV_SATURATE,
                "min_score": MIN_SCORE,
                "episode_capsule_enabled": EPISODE_CAPSULE_ON,
                "episode_capsule_query_min": EPISODE_CAPSULE_QUERY_MIN,
                "episode_capsule_assignment_min": EPISODE_CAPSULE_ASSIGNMENT_MIN,
                "episode_capsule_render_mode": EPISODE_CAPSULE_RENDER_MODE,
                "same_series_timeline_enabled": SAME_SERIES_TIMELINE_ON,
                "local_rerank_enabled": LOCAL_RERANK_ON,
                "local_rerank_model": getattr(
                    self.config.recall,
                    "local_rerank_model",
                    "nreimers/mmarco-mMiniLMv2-L6-H384-v1",
                ),
                "local_rerank_pool_factor": LOCAL_RERANK_POOL_FACTOR,
                "hierarchy_bundle_enabled": HIERARCHY_BUNDLE_ON,
                "multi_seed_bundle_enabled": MULTI_SEED_BUNDLE_ON,
                "multi_seed_bundle_mode": MULTI_SEED_BUNDLE_MODE,
            }
            # 用法 C：关系路径走**独立字段**，不参与主排序。
            # 把路径节点混进平铺的召回列表会丢掉关系语义，答案 LLM 只看到几条互不相干的记忆。
            if relation_paths:
                response.extra["related_paths"] = relation_paths
            if _graph_stats:
                response.extra["graph_rerank"] = _graph_stats

            tracer.set_output({
                "success": True,
                "total_found": response.total_found,
                "pipeline_ms": (datetime.now() - start_time).total_seconds() * 1000,
                "channels": response.extra["channels"],
            })

            logger.info(
                f"[read/{self.VERSION}] "
                f"vdb_sem={len(vdb_semantic_hits)} vdb_kw={len(vdb_keyword_hits)} "
                f"hidden_l0={len(hidden_l0_hits)} fwd_l6={len(forward_l6)} rev_l6={len(reverse_l6)} "
                f"fused_l6={len(fused_l6)} graph_intent={len(graph_intention_hits)} "
                f"returned={len(final_results)} "
                f"query='{request.query[:80]}'"
            )

            response.success = True

        except Exception as e:
            logger.error("%s.read failed: %s", self.__class__.__name__, e, exc_info=True)
            response.error_code = 500
            response.error_message = str(e)
            tracer.set_error(str(e))

        response.elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000

        # Summary trace log
        await trace_log.log_summary(
            query=request.query,
            intent=None,
            confidence=None,
            is_low_confidence=None,
            channels={
                "vdb_semantic": len(vdb_semantic_hits),
                "vdb_keyword": len(vdb_keyword_hits),
                "graph_schema": len(graph_schema_hits),
                "graph_intention": len(graph_intention_hits),
                "hidden_l0_context": len(hidden_l0_hits),
            },
            total_found=response.total_found,
            elapsed_ms=response.elapsed_ms,
            returned_memories=response.memories,
        )

        return response

    async def close(self) -> None:
        logger.debug("%s closed", self.__class__.__name__)

    # ==================================================================
    # Helpers
    # ==================================================================

    def _get_isolation_key(self, request: ReadRequest) -> str:
        """Build isolation key for graph store queries."""
        user_id = request.user_id or (request.user_ids[0] if request.user_ids else "")
        agent_id = request.agent_id or "default"
        return MemoryNode.build_isolation_key(user_id, agent_id)

    def _build_isolation_params(self, request: ReadRequest) -> Dict[str, Any]:
        """
        Build isolation parameters shared by all readers.
        """
        user_ids = request.user_ids if request.user_ids else ([request.user_id] if request.user_id else [])
        agent_ids = request.agent_ids
        session_ids = request.session_ids

        if len(agent_ids) > 1 and len(session_ids) > 1:
            return {
                "error_msg": (
                    "Cannot specify multiple agent_ids and multiple session_ids simultaneously. "
                    "Use single agent_id with multiple session_ids, or multiple agent_ids without session_ids."
                )
            }

        isolation_key = ""
        isolation_keys = None
        search_user_ids = None
        search_agent_ids = None

        if not agent_ids and not session_ids:
            if request.agent_id:
                keys = [
                    MemoryNode.build_isolation_key(u, request.agent_id or "default")
                    for u in user_ids
                ] if user_ids else None
                if keys and len(keys) == 1:
                    isolation_key = keys[0]
                else:
                    isolation_keys = keys
            else:
                search_user_ids = user_ids if user_ids else None
        elif agent_ids and not session_ids:
            search_user_ids = user_ids if user_ids else None
            search_agent_ids = agent_ids
        else:
            effective_agent_ids = agent_ids if agent_ids else [request.agent_id or "default"]
            isolation_keys = [
                MemoryNode.build_isolation_key(u, a, s)
                for u in (user_ids or ["default"])
                for a in effective_agent_ids
                for s in session_ids
            ]

        return {
            "isolation_key": isolation_key,
            "isolation_keys": isolation_keys,
            "user_ids": search_user_ids,
            "agent_ids": search_agent_ids,
        }
