"""
Reconcile 候选召回：向量 + BM25 的 additive 融合逻辑。

  1. 向量扩池（limit × multiplier）
  2. 独立 keyword 通道（若后端支持 keyword_search）
  3. 按条加成：final = sem + 𝟙[bm25]·w_bm25·bm25 + 𝟙[ent]·w_tag·max(tag_cos, entity_hit)
  4. tag 实体 cos（LLM tags）+ Graph Entity MENTIONS（正文 NER）

保留历史权重 env：
  MEMORY_HYBRID_V2_VDB_WEIGHT_BM25 / WEIGHT_TAG / TAG_COS_THR
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence, Tuple, TYPE_CHECKING

from ...models.memory import MemoryLayer, MemoryNode, MemoryStatus
from ...utils.log_setup import get_request_id
from ...utils.async_executor import to_thread_guarded
from . import bm25 as rbm25
from .lemmatize import get_bm25_params, lemmatize_for_bm25
from .ner import (
    extract_entities,
    entity_tag_pairs,
    max_entity_tag_cos,
    apply_geo_intent_prior,
)
from .scoring import normalize_bm25, score_vdb_node_additive

if TYPE_CHECKING:
    from ...data.vector_store_base import VectorStoreBase

logger = logging.getLogger(__name__)


def _float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class ReconcileRetrievalConfig:
    """Reconcile 候选召回的默认权重与阈值。"""

    limit: int = 20
    vec_pool_multiplier: int = 3
    min_score: float = 0.4
    # additive（不再用旧 convex 0.6/0.4）
    w_bm25: float = 0.2
    w_tag: float = 0.3
    tag_cos_thr: float = 0.5

    @classmethod
    def from_env(cls, **overrides) -> "ReconcileRetrievalConfig":
        base = cls(
            w_bm25=_float_env("MEMORY_HYBRID_V2_VDB_WEIGHT_BM25", 0.2),
            w_tag=_float_env("MEMORY_HYBRID_V2_VDB_WEIGHT_TAG", 0.3),
            tag_cos_thr=_float_env("MEMORY_HYBRID_V2_TAG_COS_THR", 0.5),
        )
        if not overrides:
            return base
        data = {
            "limit": base.limit,
            "vec_pool_multiplier": base.vec_pool_multiplier,
            "min_score": base.min_score,
            "w_bm25": base.w_bm25,
            "w_tag": base.w_tag,
            "tag_cos_thr": base.tag_cos_thr,
        }
        data.update(overrides)
        return cls(**data)


class ReconcileHybridRetriever:
    """对单条新 memory 文本做向量 + BM25 融合召回。"""

    def __init__(self, config: Optional[ReconcileRetrievalConfig] = None):
        self._cfg = config or ReconcileRetrievalConfig.from_env()

    @property
    def vec_pool_limit(self) -> int:
        return self._cfg.limit * self._cfg.vec_pool_multiplier

    def _log_bm25_diagnostics(
        self,
        *,
        query_text: str,
        query_lemmatized: str,
        q_terms: List[str],
        pool_size: int,
        raw_bm25: List[float],
        sem_scores: List[float],
        bm25_norms: List[float],
        fused_all: List[float],
        kept: int,
    ) -> None:
        req = get_request_id() or "-"
        q_preview = (query_text or "")[:120].replace("\n", " ")
        lem_preview = (query_lemmatized or "")[:120].replace("\n", " ")

        raw_max = max(raw_bm25) if raw_bm25 else 0.0
        raw_nonzero = sum(1 for s in raw_bm25 if s > 0)
        norm_max = max(bm25_norms) if bm25_norms else 0.0
        norm_nonzero = sum(1 for s in bm25_norms if s > 0)
        sem_max = max(sem_scores) if sem_scores else 0.0
        fused_max = max(fused_all) if fused_all else 0.0

        base = (
            f"[reconcile-hybrid][{req}] pool={pool_size} q_terms={len(q_terms)} "
            f"raw_bm25 max={raw_max:.4f} nonzero={raw_nonzero}/{len(raw_bm25)} "
            f"bm25_norm max={norm_max:.4f} nonzero={norm_nonzero}/{len(bm25_norms)} "
            f"sem_max={sem_max:.4f} fused_max={fused_max:.4f} "
            f"kept={kept}/{len(fused_all)} min_score={self._cfg.min_score} "
            f"query({len(query_text or '')} chars)"
        )
        # query 是待对账的新记忆原文，词项与预览只进 DEBUG
        logger.debug(
            f"[reconcile-hybrid][{req}] q_terms={q_terms!r} "
            f"query={q_preview!r} lemmatized={lem_preview!r}"
        )

        if not q_terms:
            logger.error(
                f"{base} | BM25_SKIP: empty query tokens after tokenize"
            )
            return

        if pool_size > 0 and raw_nonzero == 0:
            level = logger.warning if kept > 0 else logger.error
            level(
                f"{base} | BM25_ALL_ZERO: no term overlap in vec/keyword pool"
            )
            return

        if pool_size > 0 and kept == 0 and fused_all:
            logger.warning(
                f"{base} | MIN_SCORE_FILTER: all fused below "
                f"min_score={self._cfg.min_score}"
            )

    async def _keyword_channel(
        self,
        *,
        vector_store: "VectorStoreBase",
        query_lemmatized: str,
        user_id: str,
        layers: List[MemoryLayer],
        status_filter: List[MemoryStatus],
        top_k: int,
        include_sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """独立 keyword 通道；不支持则空。"""
        if not query_lemmatized or not hasattr(vector_store, "keyword_search"):
            return []
        try:
            hits = await vector_store.keyword_search(
                query=query_lemmatized,
                top_k=top_k,
                user_id=user_id,
                # 与 vec 主路一致：user 维度，不按 agent 收窄
                agent_ids=None,
                layers=layers,
                status_filter=status_filter,
                only_latest=False,
                include_sources=include_sources,
            )
            return hits or []
        except Exception as e:
            logger.debug("[reconcile-hybrid] keyword_search skipped: %s", e)
            return []

    async def _tag_and_entity_boosts(
        self,
        *,
        query_text: str,
        nodes_by_id: Dict[str, MemoryNode],
        embed_service: Any,
        graph_store: Any,
        user_id: str,
        agent_id: Optional[str],
    ) -> Tuple[Dict[str, float], Dict[str, float]]:
        """返回 (tag_cos_by_id, entity_boost_by_id)。"""
        tag_cos_by_id: Dict[str, float] = {}
        entity_boost_by_id: Dict[str, float] = {}
        query_ents = extract_entities(query_text or "", for_boost=True)
        nodes_list = list(nodes_by_id.values()) if nodes_by_id else []
        geo_prior = float(
            getattr(self._cfg, "geo_intent_prior", None)
            or os.environ.get("MEMORY_HYBRID_V2_GEO_INTENT_PRIOR", "0.6")
        )
        if not query_ents:
            if geo_prior > 0 and nodes_list:
                tag_cos_by_id = apply_geo_intent_prior(
                    query_text or "",
                    query_ents,
                    nodes_list,
                    tag_cos_by_id,
                    prior=geo_prior,
                )
            return tag_cos_by_id, entity_boost_by_id

        # --- tag 实体 embedding cos ---
        if embed_service is not None and nodes_by_id:
            emb_texts: List[str] = []
            for span, _lab in query_ents:
                if span and span not in emb_texts:
                    emb_texts.append(span)
            mem_ent_map: Dict[str, List[Tuple[str, str]]] = {}
            for nid, node in nodes_by_id.items():
                tags = list(getattr(node, "tags", None) or [])
                ners = list(getattr(node, "tag_ners", None) or [])
                ents = entity_tag_pairs(tags, ners if ners else None)
                mem_ent_map[nid] = ents
                for t_text, _ in ents:
                    if t_text and t_text not in emb_texts:
                        emb_texts.append(t_text)
            emb_map: Dict[str, List[float]] = {}
            if emb_texts:
                try:
                    vecs = await embed_service.embed_batch(emb_texts)
                    for t, v in zip(emb_texts, vecs or []):
                        if v:
                            emb_map[t] = v
                except Exception as e:
                    logger.debug("[reconcile-hybrid] tag embed failed: %s", e)
            if emb_map:
                for nid, ments in mem_ent_map.items():
                    cos, _pair = max_entity_tag_cos(
                        query_ents, ments, emb_map,
                        min_cos=self._cfg.tag_cos_thr, require_same_group=True,
                    )
                    if cos > 0:
                        tag_cos_by_id[nid] = cos

        # --- Graph Entity MENTIONS ---
        if graph_store is not None and hasattr(graph_store, "find_memories_by_entities"):
            try:
                scope = MemoryNode.build_topic_scope_key(
                    user_id, agent_id or "default_agent",
                )
                hits = await graph_store.find_memories_by_entities(
                    scope, query_ents, limit=max(100, len(nodes_by_id) * 2),
                )
                for h in hits or []:
                    nid = h.get("node_id")
                    if not nid:
                        continue
                    sc = float(h.get("match_score") or 1.0)
                    if sc > entity_boost_by_id.get(nid, 0.0):
                        entity_boost_by_id[nid] = sc
            except Exception as e:
                logger.debug("[reconcile-hybrid] entity lookup failed: %s", e)

        return tag_cos_by_id, entity_boost_by_id

    async def search_candidates(
        self,
        query_text: str,
        query_embedding: List[float],
        *,
        vector_store: "VectorStoreBase",
        user_id: str,
        agent_id: Optional[str],
        layers: List[MemoryLayer],
        embed_service: Any = None,
        graph_store: Any = None,
        include_sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Returns:
            [{"node", "score", "_sem", "_bm25", "_tag_cos", "_entity_boost"}, ...]
            按融合分降序，长度 ≤ limit，且 score >= min_score。

        候选范围是 **user 维度**（不下发 agent_id 过滤）—— 与原先 reconcile 契约一致。
        """
        if not query_text.strip() or not query_embedding:
            return []

        from ...core.workload import reconcile_retrieval_slot

        # Local Qdrant performs full-array scoring/filtering for every query.
        # A chunk with 20 facts otherwise launches roughly 40 collection scans,
        # and eight rollouts multiply that into hundreds of simultaneous CPU jobs.
        local_store = bool(getattr(vector_store, "_is_local", False))
        async with reconcile_retrieval_slot(local=local_store):
            return await self._search_candidates_unbounded(
                query_text=query_text,
                query_embedding=query_embedding,
                vector_store=vector_store,
                user_id=user_id,
                agent_id=agent_id,
                layers=layers,
                embed_service=embed_service,
                graph_store=graph_store,
                include_sources=include_sources,
            )

    async def search_candidates_batch(
        self,
        query_texts: Sequence[str],
        query_embeddings: Sequence[List[float]],
        *,
        vector_store: "VectorStoreBase",
        user_id: str,
        agent_id: Optional[str],
        layers: List[MemoryLayer],
        embed_service: Any = None,
        graph_store: Any = None,
        include_sources: Optional[List[str]] = None,
    ) -> List[List[Dict[str, Any]]]:
        """Retrieve candidates for one extracted chunk without per-fact RPC fan-out.

        Every query keeps its own filter, candidate pool, score calibration and
        final top-k.  Only the transport is collapsed when the backend exposes
        native batch methods.  Local/non-batch stores use the scalar path.
        """
        texts = list(query_texts)
        embeddings = list(query_embeddings)
        if len(texts) != len(embeddings):
            raise ValueError("query_texts and query_embeddings must have equal length")
        if not texts:
            return []

        local_store = bool(getattr(vector_store, "_is_local", False))
        if local_store:
            return list(await asyncio.gather(*[
                self.search_candidates(
                    text,
                    embedding,
                    vector_store=vector_store,
                    user_id=user_id,
                    agent_id=agent_id,
                    layers=layers,
                    embed_service=embed_service,
                    graph_store=graph_store,
                    include_sources=include_sources,
                )
                for text, embedding in zip(texts, embeddings)
            ]))

        from ...core.workload import reconcile_retrieval_slot

        async with reconcile_retrieval_slot(local=False):
            query_lemmatized = await to_thread_guarded(
                lambda: [lemmatize_for_bm25(text) for text in texts]
            )
            status_filter = [MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED]
            dense_requests = [
                {
                    "query_embedding": embedding,
                    "user_id": user_id,
                    "agent_ids": None,
                    "layers": layers,
                    "limit": self.vec_pool_limit,
                    "score_threshold": 0.0,
                    "status_filter": status_filter,
                    "only_latest": False,
                    "include_sources": include_sources,
                }
                for embedding in embeddings
            ]
            keyword_requests = [
                {
                    "query": lemmatized,
                    "top_k": max(self._cfg.limit * 4, 60),
                    "user_id": user_id,
                    "agent_ids": None,
                    "layers": layers,
                    "status_filter": status_filter,
                    "only_latest": False,
                    "include_sources": include_sources,
                }
                for lemmatized in query_lemmatized
            ]
            dense_batch = getattr(vector_store, "search_batch", None)
            keyword_batch = getattr(vector_store, "keyword_search_batch", None)
            dense_call = (
                dense_batch(dense_requests)
                if callable(dense_batch)
                else asyncio.gather(*[
                    vector_store.search(**request) for request in dense_requests
                ])
            )
            keyword_call = (
                keyword_batch(keyword_requests)
                if callable(keyword_batch)
                else asyncio.gather(*[
                    self._keyword_channel(
                        vector_store=vector_store,
                        query_lemmatized=request["query"],
                        user_id=user_id,
                        layers=layers,
                        status_filter=status_filter,
                        top_k=request["top_k"],
                        include_sources=request.get("include_sources"),
                    )
                    for request in keyword_requests
                ])
            )
            dense_result, keyword_result = await asyncio.gather(
                dense_call,
                keyword_call,
                return_exceptions=True,
            )
            if isinstance(dense_result, Exception):
                logger.warning("[reconcile-hybrid] dense batch failed: %s", dense_result)
                dense_result = [[] for _ in texts]
            if isinstance(keyword_result, Exception):
                logger.debug("[reconcile-hybrid] keyword batch failed: %s", keyword_result)
                keyword_result = [[] for _ in texts]

            dense_rows = list(dense_result)
            keyword_rows = list(keyword_result)
            dense_rows.extend([[]] * (len(texts) - len(dense_rows)))
            keyword_rows.extend([[]] * (len(texts) - len(keyword_rows)))

            # Candidate content repeats heavily across facts and sibling
            # rollouts.  Warm the immutable NLP cache off-loop once per chunk
            # so the fusion loop does not monopolize asyncio's only thread.
            unique_contents = {
                str(getattr(hit.get("node"), "content", "") or "")
                for rows in (dense_rows, keyword_rows)
                for row in rows
                for hit in row or []
                if hit.get("node") is not None
            }
            if unique_contents:
                await to_thread_guarded(
                    lambda: [lemmatize_for_bm25(text) for text in unique_contents]
                )

            return list(await asyncio.gather(*[
                self._fuse_candidates(
                    query_text=text,
                    query_lemmatized=lemmatized,
                    vec_results=dense_rows[index],
                    kw_results=keyword_rows[index],
                    vector_store=vector_store,
                    user_id=user_id,
                    agent_id=agent_id,
                    embed_service=embed_service,
                    graph_store=graph_store,
                )
                for index, (text, lemmatized) in enumerate(
                    zip(texts, query_lemmatized)
                )
            ]))

    async def _search_candidates_unbounded(
        self,
        query_text: str,
        query_embedding: List[float],
        *,
        vector_store: "VectorStoreBase",
        user_id: str,
        agent_id: Optional[str],
        layers: List[MemoryLayer],
        embed_service: Any = None,
        graph_store: Any = None,
        include_sources: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:

        status_filter = [MemoryStatus.ACTIVE, MemoryStatus.SUPERSEDED]
        vec_limit = self.vec_pool_limit
        kw_limit = max(self._cfg.limit * 4, 60)
        query_lemmatized = lemmatize_for_bm25(query_text)

        vec_task = vector_store.search(
            query_embedding=query_embedding,
            user_id=user_id,
            agent_ids=None,
            layers=layers,
            limit=vec_limit,
            score_threshold=0.0,
            status_filter=status_filter,
            only_latest=False,
            include_sources=include_sources,
        )
        kw_task = self._keyword_channel(
            vector_store=vector_store,
            query_lemmatized=query_lemmatized,
            user_id=user_id,
            layers=layers,
            status_filter=status_filter,
            top_k=kw_limit,
            include_sources=include_sources,
        )
        vec_results, kw_results = await asyncio.gather(
            vec_task, kw_task, return_exceptions=True,
        )
        if isinstance(vec_results, Exception):
            logger.warning("[reconcile-hybrid] vec search failed: %s", vec_results)
            vec_results = []
        if isinstance(kw_results, Exception):
            logger.debug("[reconcile-hybrid] kw search failed: %s", kw_results)
            kw_results = []

        return await self._fuse_candidates(
            query_text=query_text,
            query_lemmatized=query_lemmatized,
            vec_results=vec_results,
            kw_results=kw_results,
            vector_store=vector_store,
            user_id=user_id,
            agent_id=agent_id,
            embed_service=embed_service,
            graph_store=graph_store,
        )

    async def _fuse_candidates(
        self,
        *,
        query_text: str,
        query_lemmatized: str,
        vec_results: Sequence[Dict[str, Any]],
        kw_results: Sequence[Dict[str, Any]],
        vector_store: "VectorStoreBase",
        user_id: str,
        agent_id: Optional[str],
        embed_service: Any = None,
        graph_store: Any = None,
    ) -> List[Dict[str, Any]]:
        """Fuse one query's already-fetched dense and keyword candidates."""

        # keyword score normalize
        kw_normalized = getattr(vector_store, "keyword_score_normalized", False)
        midpoint, steepness = get_bm25_params(query_text, query_lemmatized)
        keyword_scores: Dict[str, float] = {}
        kw_nodes: Dict[str, MemoryNode] = {}
        for r in kw_results or []:
            node = r.get("node")
            nid = r.get("node_id") or (getattr(node, "node_id", None) if node else None)
            if not nid:
                continue
            raw = float(r.get("score", 0.0) or 0.0)
            if kw_normalized:
                keyword_scores[nid] = max(0.0, min(raw, 1.0))
            else:
                keyword_scores[nid] = normalize_bm25(raw, midpoint, steepness)
            if node is not None:
                kw_nodes[nid] = node

        # semantic pool
        sem_by_id: Dict[str, float] = {}
        nodes_by_id: Dict[str, MemoryNode] = {}
        for r in vec_results or []:
            node = r.get("node")
            if node is None:
                continue
            nid = node.node_id
            nodes_by_id[nid] = node
            sem_by_id[nid] = float(r.get("score", 0.0) or 0.0)

        # keyword-only nodes: need content for in-pool BM25 fallback; merge in
        missing = [nid for nid in keyword_scores if nid not in nodes_by_id]
        if missing:
            for nid in missing:
                if nid in kw_nodes:
                    nodes_by_id[nid] = kw_nodes[nid]
                    sem_by_id.setdefault(nid, 0.0)
            still = [nid for nid in missing if nid not in nodes_by_id]
            if still:
                try:
                    fetched = await vector_store.get_by_ids(still)
                    for n in fetched or []:
                        if n is None:
                            continue
                        nodes_by_id[n.node_id] = n
                        sem_by_id.setdefault(n.node_id, 0.0)
                except Exception as e:
                    logger.debug("[reconcile-hybrid] get_by_ids for kw-only failed: %s", e)

        if not nodes_by_id:
            return []

        # 池内 BM25（与旧 reconcile 一致，补 keyword 通道未覆盖的项）
        q_terms = rbm25.tokenize(query_lemmatized or query_text)
        ordered_ids = list(nodes_by_id.keys())
        contents_lem = [
            lemmatize_for_bm25(nodes_by_id[nid].content or "")
            for nid in ordered_ids
        ]
        raw_bm25 = rbm25.compute_bm25_scores(q_terms, contents_lem)
        pool_bm25: Dict[str, float] = {}
        for i, nid in enumerate(ordered_ids):
            pool_bm25[nid] = normalize_bm25(raw_bm25[i], midpoint, steepness)

        # 每条记忆的 bm25：keyword 通道分与池内 BM25 取 max
        bm25_by_id: Dict[str, float] = {}
        for nid in ordered_ids:
            bm25_by_id[nid] = max(
                keyword_scores.get(nid, 0.0),
                pool_bm25.get(nid, 0.0),
            )

        tag_cos_by_id, entity_boost_by_id = await self._tag_and_entity_boosts(
            query_text=query_text,
            nodes_by_id=nodes_by_id,
            embed_service=embed_service,
            graph_store=graph_store,
            user_id=user_id,
            agent_id=agent_id,
        )

        # Entity 命中但不在池内：从 VDB 补进（sem=0 + entity boost）
        for nid, esc in entity_boost_by_id.items():
            if nid in nodes_by_id:
                continue
            try:
                fetched = await vector_store.get_by_ids([nid])
                n = (fetched or [None])[0]
                if n is None:
                    continue
                st = getattr(n, "status", None)
                if st is not None and getattr(st, "value", "") == MemoryStatus.SHADOW.value:
                    continue
                nodes_by_id[nid] = n
                sem_by_id[nid] = 0.0
                bm25_by_id[nid] = 0.0
            except Exception:
                continue

        fused_hits: List[Dict[str, Any]] = []
        fused_all: List[float] = []
        sem_scores_dbg: List[float] = []
        bm25_norms_dbg: List[float] = []
        raw_dbg: List[float] = []

        for nid, node in nodes_by_id.items():
            sem = float(sem_by_id.get(nid, 0.0))
            bm25 = float(bm25_by_id.get(nid, 0.0))
            fused_ent = max(
                tag_cos_by_id.get(nid, 0.0),
                entity_boost_by_id.get(nid, 0.0),
            )
            fused = score_vdb_node_additive(
                sem,
                bm25_score=bm25,
                tag_cos=fused_ent,
                w_bm25=self._cfg.w_bm25,
                w_tag=self._cfg.w_tag,
            )
            fused_all.append(fused)
            sem_scores_dbg.append(sem)
            bm25_norms_dbg.append(bm25)
            raw_dbg.append(bm25)  # diagnostics only
            if fused < self._cfg.min_score:
                continue
            fused_hits.append({
                "node": node,
                "score": fused,
                "_sem": sem,
                "_bm25": bm25,
                "_tag_cos": tag_cos_by_id.get(nid, 0.0),
                "_entity_boost": entity_boost_by_id.get(nid, 0.0),
            })

        self._log_bm25_diagnostics(
            query_text=query_text,
            query_lemmatized=query_lemmatized,
            q_terms=q_terms,
            pool_size=len(nodes_by_id),
            raw_bm25=raw_dbg,
            sem_scores=sem_scores_dbg,
            bm25_norms=bm25_norms_dbg,
            fused_all=fused_all,
            kept=len(fused_hits),
        )

        if fused_hits and logger.isEnabledFor(logging.DEBUG):
            req = get_request_id() or "-"
            top = fused_hits[0]
            logger.debug(
                f"[reconcile-hybrid][{req}] ok pool={len(nodes_by_id)} "
                f"kept={len(fused_hits)} top_fused={top['score']:.4f} "
                f"sem={top['_sem']:.4f} bm25={top['_bm25']:.4f} "
                f"tag={top.get('_tag_cos', 0):.4f} ent={top.get('_entity_boost', 0):.4f}"
            )

        fused_hits.sort(key=lambda x: x["score"], reverse=True)
        return fused_hits[: self._cfg.limit]
