"""
Cross-Domain Schema Induction Sweeper

跨域突破性归纳：从用户的 L6 basic Schema 中发现深层行为同构，生成 L6 core Schema。

算法三部曲：
  Step 1: 行为升维打标 — 对 L6 basic 调 LLM 生成行为心理学抽象 → embed → V_beh
  Step 2: 碰撞扫描 — 矩阵化 cosine 碰撞 + Union-Find 聚类 + Core 融合
  Step 3: LLM 突破性归纳 — 对碰撞 cluster 调 LLM 生成 L6 core

触发时机：evolve() 流程末尾，System2 Agent 执行完毕后
触发条件：用户 L6 basic 节点数 >= MIN_BASIC_FOR_SWEEP（默认 5）

Trace 设计：
  - 与 System2 Agent 共用同一个 request_id，从 write request 可穿起全链路
  - 每个 trace 带 memory_ids，从任意 memory_id 可反查经过的所有 step
  - pipeline_log steps: SWEEPER_SUMMARY / SWEEPER_BEH_EMBED / SWEEPER_COLLISION / SWEEPER_CORE_CREATE / SWEEPER_CORE_MERGE
  - memory_operation ops: SWEEPER_BEH_EMBED / SWEEPER_CORE_CREATE / SWEEPER_CROSS_EDGE / SWEEPER_CORE_MERGE
"""

import os
import json
import uuid
import logging
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

from ..models.memory import MemoryLayer, MemoryNode, MemoryStatus, SourceType
from ..data.graph_store_base import GraphStoreBase
from ..core.embed_service import EmbedService
from ..utils.source_filter import merge_sources

logger = logging.getLogger(__name__)

# ====================================================================
# 配置
# ====================================================================

_CROSS_DOMAIN_ENABLE = os.getenv("MEMORY_CROSS_DOMAIN_ENABLE", "true").lower() == "true"
_MIN_BASIC_FOR_SWEEP = int(os.getenv("MEMORY_CROSS_DOMAIN_MIN_BASICS", "5"))
_BEH_THRESHOLD = float(os.getenv("MEMORY_CROSS_DOMAIN_BEH_THRESHOLD", "0.70"))
_CON_THRESHOLD = float(os.getenv("MEMORY_CROSS_DOMAIN_CON_THRESHOLD", "0.60"))
_ANN_TOPK = int(os.getenv("MEMORY_CROSS_DOMAIN_ANN_TOPK", "10"))

# ====================================================================
# LLM Prompts
# ====================================================================

_BEHAVIOR_ABSTRACTION_PROMPT = """Task: You are a behavioral psychologist. Abstract the following schema into a pure psychological/behavioral description. Strip ALL domain-specific nouns and scenarios — output ONLY the underlying behavioral style and psychological motivation.

CRITICAL LANGUAGE RULE: Your output MUST be in the SAME language as the input schema below. If the input is in English, you MUST output in English. If the input is in Chinese, you MUST output in Chinese. Violating this rule invalidates the output.

Input schema:
{content}

Output (strict JSON, same language as input):
{{"abstraction_for_embedding": "...pure behavioral description, same language as input..."}}"""

_CROSS_DOMAIN_INDUCTION_PROMPT = """Task: You are a deep pattern analyst. The system has detected structural resonance among the following schemas from different areas of the user's life. On the surface they appear unrelated, but their underlying behavioral logic is strikingly similar.

Your job: synthesize a HIGHER-ORDER pattern that explains WHY these behaviors co-occur — something the user themselves may not be consciously aware of. Think in terms of:
- Deep cognitive style (how they process information and make decisions)
- Core psychological need (what drives them at a fundamental level)
- Hidden mental model (the implicit belief system behind these behaviors)

Be insightful. Be precise. Quality over quantity — only output a synthesis if the connection is genuinely compelling and logically airtight. If the evidence is weak or the connection is superficial, output null.

LANGUAGE RULE: Your output (core_pattern, reasoning) MUST be in the SAME language as the input schemas below. If the schemas are in Chinese, output in Chinese. If in English, output in English. Do NOT translate or switch language.

Input schemas:
{patterns}

Output (strict JSON, or null if no compelling synthesis):
{{"core_pattern": "...one sentence describing the higher-order pattern, same language as input...", "reasoning": "...why these schemas are connected at a deep level, same language as input...", "confidence": 0.85}}"""


# ====================================================================
# Union-Find
# ====================================================================

class UnionFind:
    """Union-Find (Disjoint Set Union) for merging collision pairs."""

    def __init__(self):
        self._parent: Dict[str, str] = {}

    def find(self, x: str) -> str:
        if x not in self._parent:
            self._parent[x] = x
        while self._parent[x] != x:
            self._parent[x] = self._parent[self._parent[x]]
            x = self._parent[x]
        return x

    def union(self, x: str, y: str) -> None:
        rx, ry = self.find(x), self.find(y)
        if rx != ry:
            self._parent[ry] = rx

    def clusters(self) -> Dict[str, List[str]]:
        groups: Dict[str, List[str]] = {}
        for x in self._parent:
            root = self.find(x)
            groups.setdefault(root, []).append(x)
        return groups


# ====================================================================
# CrossDomainSweeper
# ====================================================================

class CrossDomainSweeper:
    """跨域突破性归纳扫描器"""

    def __init__(
        self,
        graph_store: GraphStoreBase,
        embed_service: EmbedService,
        llm_call,  # async callable(prompt: str) -> str
        user_id: str,
        agent_id: str,
        request_id: str = "",
        cache: Any = None,
    ):
        self.graph_store = graph_store
        self.embed_service = embed_service
        self.llm_call = llm_call
        self.user_id = user_id
        self.agent_id = agent_id
        self.request_id = request_id
        self._cache = cache
        self._isolation_key = MemoryNode.build_isolation_key(user_id, agent_id)

    # ================================================================
    # Trace helpers
    # ================================================================

    async def _log_pipeline(
        self, step: str, prompt: str = "", response: str = "",
        parsed: Optional[Dict] = None, memory_ids: Optional[List[str]] = None,
        elapsed_ms: float = 0,
    ) -> None:
        """写 pipeline_log（best-effort）"""
        if not self._cache or not self.request_id:
            return
        try:
            await self._cache.store_pipeline_log(
                request_id=self.request_id,
                user_id=self.user_id,
                agent_id=self.agent_id,
                step=step,
                prompt=prompt,
                response=response,
                parsed=json.dumps(parsed or {}, ensure_ascii=False, default=str),
                memory_ids=memory_ids,
                elapsed_ms=elapsed_ms,
            )
        except Exception as e:
            logger.debug(f"[sweeper] store_pipeline_log({step}) failed: {e}")

    async def _log_mem_op(
        self, op: str, memory_id: str, content: str = "",
        reason: str = "",
    ) -> None:
        """写 memory_operation（best-effort）"""
        if not self._cache or not self.request_id:
            return
        try:
            await self._cache.store_memory_operation(
                request_id=self.request_id,
                user_id=self.user_id,
                agent_id=self.agent_id,
                op=op,
                memory_id=memory_id,
                content=content[:500],
                layer="graph",
                reason=reason,
            )
        except Exception as e:
            logger.debug(f"[sweeper] store_memory_operation({op}) failed: {e}")

    # ================================================================
    # Main entry
    # ================================================================

    async def sweep(self) -> Dict[str, Any]:
        """执行一次跨域扫描，返回统计信息"""
        _t_start = datetime.now()

        if not _CROSS_DOMAIN_ENABLE:
            return {"skipped": True, "reason": "disabled"}

        # 获取所有 L6 basic 节点
        all_basics = await self.graph_store.get_all_nodes(
            isolation_key=self._isolation_key,
            layer=MemoryLayer.L6_SCHEMA,
            status=MemoryStatus.ACTIVE,
            limit=500,
        )
        basics = [
            n for n in all_basics
            # 不再排除 core — 所有 L6 节点都参与碰撞（支持递归升级）
        ]

        if not basics:
            return {"skipped": True, "reason": "no L6 basics"}

        logger.info(f"[sweeper] Starting cross-domain sweep: {len(basics)} L6 basics")

        # Step 1: 确保所有 basic 都有 beh_embedding（无论数量多少都要做）
        new_beh_count = await self._ensure_beh_embeddings(basics)

        # Step 2: 碰撞扫描（需要足够数量才有意义）
        if len(basics) < _MIN_BASIC_FOR_SWEEP:
            result = {
                "basics_count": len(basics),
                "new_beh_embeddings": new_beh_count,
                "collisions": 0,
                "cores_created": 0,
                "cores_merged": 0,
                "collision_skipped": f"only {len(basics)} basics (need {_MIN_BASIC_FOR_SWEEP} for collision)",
            }
            elapsed_ms = (datetime.now() - _t_start).total_seconds() * 1000
            await self._log_pipeline(
                step="SWEEPER_SUMMARY", parsed=result, elapsed_ms=elapsed_ms,
            )
            return result

        collision_clusters, collision_details = await self._scan_collisions(basics)

        # Step 2 trace: 碰撞详情
        if collision_details:
            await self._log_pipeline(
                step="SWEEPER_COLLISION",
                parsed={
                    "total_basics_with_beh": collision_details.get("total_with_beh", 0),
                    "collision_pairs": collision_details.get("pairs", []),
                    "clusters_count": len(collision_clusters),
                },
                memory_ids=[
                    nid for cluster in collision_clusters for nid in cluster
                ],
            )

        if not collision_clusters:
            result = {
                "basics_count": len(basics),
                "new_beh_embeddings": new_beh_count,
                "collisions": 0,
                "cores_created": 0,
                "cores_merged": 0,
            }
            elapsed_ms = (datetime.now() - _t_start).total_seconds() * 1000
            await self._log_pipeline(
                step="SWEEPER_SUMMARY", parsed=result, elapsed_ms=elapsed_ms,
            )
            return result

        # Step 3: LLM 归纳 + 图写入
        cores_created = 0
        cores_merged = 0
        created_memory_ids = []
        for cluster_ids in collision_clusters:
            created, merged, new_ids = await self._process_cluster(cluster_ids, basics)
            cores_created += created
            cores_merged += merged
            created_memory_ids.extend(new_ids)

        elapsed_ms = (datetime.now() - _t_start).total_seconds() * 1000
        result = {
            "basics_count": len(basics),
            "new_beh_embeddings": new_beh_count,
            "collisions": len(collision_clusters),
            "cores_created": cores_created,
            "cores_merged": cores_merged,
            "created_memory_ids": created_memory_ids,
        }

        # SWEEPER_SUMMARY trace
        await self._log_pipeline(
            step="SWEEPER_SUMMARY", parsed=result, elapsed_ms=elapsed_ms,
        )

        logger.info(f"[sweeper] Sweep complete: {result}")
        return result

    # ================================================================
    # Step 1: 行为升维打标
    # ================================================================

    async def _ensure_beh_embeddings(self, basics: List[MemoryNode]) -> int:
        """确保每个 L6 basic 都有 beh_embedding，没有的就生成"""
        count = 0
        for node in basics:
            custom = getattr(node, "custom", None) or {}
            if custom.get("_has_beh"):
                continue

            try:
                # LLM 行为升维
                prompt = _BEHAVIOR_ABSTRACTION_PROMPT.format(content=node.content)
                llm_response = await self.llm_call(prompt)
                # 兼容 LLM 返回 markdown code fence 包裹的 JSON
                _resp_text = llm_response.strip()
                if _resp_text.startswith("```"):
                    _lines = _resp_text.split("\n")
                    _lines = _lines[1:]  # 去掉 ```json
                    if _lines and _lines[-1].strip() == "```":
                        _lines = _lines[:-1]
                    _resp_text = "\n".join(_lines).strip()
                parsed = json.loads(_resp_text)
                abstraction = parsed.get("abstraction_for_embedding", "")
                if not abstraction:
                    continue

                # Embed → V_beh
                beh_embedding, beh_embed_meta = await self.embed_service.embed_queued_with_meta(
                    abstraction
                )

                # 写入 Graph 节点
                await self.graph_store.update_embedding(
                    node.node_id, beh_embedding=beh_embedding
                )

                # 标记已生成 + 保存行为抽象文本
                custom["_has_beh"] = True
                custom["beh_abstraction"] = abstraction
                await self.graph_store.update_node(
                    node.node_id, {"custom_json": json.dumps(custom, ensure_ascii=False)}
                )

                # Trace: 每个 beh_embedding 写入记 memory_operation
                await self._log_mem_op(
                    op="SWEEPER_BEH_EMBED",
                    memory_id=node.node_id,
                    content=json.dumps({
                        "abstraction": abstraction,
                        "original_content": node.content[:200],
                    }, ensure_ascii=False),
                    reason="behavior abstraction → beh_embedding",
                )

                # Trace: pipeline_log 记 LLM prompt/response
                await self._log_pipeline(
                    step="SWEEPER_BEH_EMBED",
                    prompt=prompt,
                    response=llm_response,
                    parsed={
                        "abstraction": abstraction,
                        "node_id": node.node_id,
                        "provider_meta": beh_embed_meta,
                    },
                    memory_ids=[node.node_id],
                )

                count += 1
                logger.debug(f"[sweeper] beh_embedding set for {node.node_id[:12]}")
            except Exception as e:
                logger.warning(f"[sweeper] beh_embedding failed for {node.node_id[:12]}: {e}")

        return count

    async def _fetch_basics_with_beh(self) -> List[Dict[str, Any]]:
        """获取所有有 beh_embedding 的 L6 basic 节点（统一接口，支持 Neo4j / Kuzu）"""
        gs = self.graph_store
        results = []

        # Neo4j 后端
        if hasattr(gs, '_run'):
            rows = await gs._run(
                "MATCH (m:Memory) WHERE m.isolation_key = $ik "
                "AND m.layer = 'l6_schema' AND m.status = 'active' "
                "AND m.beh_embedding IS NOT NULL "
                "RETURN m.node_id AS node_id, m.content AS content, "
                "m.embedding AS embedding, m.beh_embedding AS beh_embedding",
                {"ik": self._isolation_key},
            )
            for r in rows:
                results.append({
                    "node_id": r["node_id"],
                    "content": r["content"],
                    "embedding": r["embedding"],
                    "beh_embedding": r["beh_embedding"],
                })
            return results

        # Kuzu 后端
        if hasattr(gs, '_execute') and hasattr(gs, '_available') and gs._available:
            r = gs._execute(
                "MATCH (m:Memory) WHERE m.isolation_key = $ik "
                "AND m.layer = 'l6_schema' AND m.status = 'active' "
                "AND m.beh_embedding IS NOT NULL "
                "RETURN m.node_id, m.content, m.embedding, m.beh_embedding;",
                {"ik": self._isolation_key},
            )
            while r is not None and r.has_next():
                row = r.get_next()
                results.append({
                    "node_id": row[0],
                    "content": row[1],
                    "embedding": row[2],
                    "beh_embedding": row[3],
                })
            return results

        logger.warning("[sweeper] Unknown graph_store backend, cannot fetch beh_embeddings")
        return []

    # ================================================================
    # Step 2: 碰撞扫描
    # ================================================================

    async def _scan_collisions(
        self,
        basics: List[MemoryNode],
    ) -> Tuple[List[List[str]], Dict[str, Any]]:
        """矩阵化碰撞扫描 + Union-Find 聚类。返回 (clusters, details)"""
        import numpy as np

        # 从 Graph 取所有有 beh_embedding 的 L6 basic（统一走抽象接口）
        all_with_beh = []
        try:
            all_with_beh = await self._fetch_basics_with_beh()
        except Exception as e:
            logger.warning(f"[sweeper] _scan_collisions fetch failed: {e}")
            return [], {}

        N = len(all_with_beh)
        if N < 2:
            return [], {"total_with_beh": N}

        # 构建矩阵
        beh_matrix = np.array(
            [np.array(d["beh_embedding"], dtype=np.float32) for d in all_with_beh]
        )
        con_matrix = np.array(
            [np.array(d["embedding"], dtype=np.float32) for d in all_with_beh]
        )

        # L2 归一化
        beh_norms = np.linalg.norm(beh_matrix, axis=1, keepdims=True) + 1e-10
        con_norms = np.linalg.norm(con_matrix, axis=1, keepdims=True) + 1e-10
        beh_normed = beh_matrix / beh_norms
        con_normed = con_matrix / con_norms

        # N×N cosine similarity
        beh_sim = beh_normed @ beh_normed.T
        con_sim = con_normed @ con_normed.T

        # 碰撞条件
        mask = (beh_sim > _BEH_THRESHOLD) & (con_sim < _CON_THRESHOLD) & ~np.eye(N, dtype=bool)
        hit_indices = np.argwhere(mask)

        if len(hit_indices) == 0:
            logger.debug(f"[sweeper] No collisions found (N={N})")
            return [], {"total_with_beh": N}

        # 预加载所有节点的 CROSS_ABSTRACTS_TO 目标（用于过滤已有关系的对）
        node_ids = [d["node_id"] for d in all_with_beh]
        targets_map: Dict[str, set] = {}  # node_id → set of target_ids
        for nid in node_ids:
            try:
                targets = await self.graph_store.get_cross_abstracts_targets(nid)
                targets_map[nid] = set(targets)
            except Exception:
                targets_map[nid] = set()

        # 收集碰撞对详情（用于 trace）
        # 过滤掉已有直接 CROSS_ABSTRACTS_TO 关系的对（A→B 或 B→A）
        pairs_detail = []
        uf = UnionFind()
        for i, j in hit_indices:
            if i < j:
                nid_i = all_with_beh[i]["node_id"]
                nid_j = all_with_beh[j]["node_id"]

                # 跳过已有直接边关系的对（防止 parent-child 再碰撞）
                if nid_j in targets_map.get(nid_i, set()):
                    continue  # A→B 已存在
                if nid_i in targets_map.get(nid_j, set()):
                    continue  # B→A 已存在
                # 跳过共享同一 target 的对（已被合成过）
                shared_targets = targets_map.get(nid_i, set()) & targets_map.get(nid_j, set())
                if shared_targets:
                    continue  # A→C, B→C 都存在

                uf.union(nid_i, nid_j)
                pairs_detail.append({
                    "a": nid_i[:12], "b": nid_j[:12],
                    "beh_sim": round(float(beh_sim[i, j]), 4),
                    "con_sim": round(float(con_sim[i, j]), 4),
                })
                logger.debug(
                    f"[sweeper] Collision: {nid_i[:12]} ↔ {nid_j[:12]} "
                    f"(beh={beh_sim[i,j]:.3f}, con={con_sim[i,j]:.3f})"
                )

        clusters = uf.clusters()
        result_clusters = [ids for ids in clusters.values() if len(ids) >= 2]
        logger.info(f"[sweeper] Found {len(result_clusters)} collision clusters from {len(pairs_detail)} pairs")

        details = {
            "total_with_beh": N,
            "pairs": pairs_detail[:50],  # 截断防止 trace 过大
            "beh_threshold": _BEH_THRESHOLD,
            "con_threshold": _CON_THRESHOLD,
        }
        return result_clusters, details

    # ================================================================
    # Step 3: LLM 归纳 + 图写入
    # ================================================================

    async def _process_cluster(
        self,
        cluster_ids: List[str],
        all_basics: List[MemoryNode],
    ) -> Tuple[int, int, List[str]]:
        """处理一个碰撞 cluster，返回 (cores_created, cores_merged, created_memory_ids)"""
        basic_map = {n.node_id: n for n in all_basics}
        # 查已有 core
        existing_core_ids = set()
        for nid in cluster_ids:
            targets = await self.graph_store.get_cross_abstracts_targets(nid)
            existing_core_ids.update(targets)

        if existing_core_ids:
            # 已有 Core → 融合（把 cluster 中未连接的节点补边到已有 core）
            core_id = list(existing_core_ids)[0]
            core_node = await self.graph_store.get_node(core_id)
            merged_ids = []
            for nid in cluster_ids:
                # 防止自环：节点本身就是 core 时不加边
                if nid == core_id:
                    continue
                targets = await self.graph_store.get_cross_abstracts_targets(nid)
                if core_id not in targets:
                    await self.graph_store.add_cross_abstracts_to(nid, core_id)
                    merged_ids.append(nid)

                    # Trace: 每条边记 memory_operation（basic_id + core_id 都可追溯）
                    await self._log_mem_op(
                        op="SWEEPER_CORE_MERGE",
                        memory_id=nid,
                        content=json.dumps({"basic_id": nid, "core_id": core_id}, ensure_ascii=False),
                        reason=f"merged basic → existing core {core_id[:12]}",
                    )

            # Trace: pipeline_log 记融合总览
            await self._log_pipeline(
                step="SWEEPER_CORE_MERGE",
                parsed={
                    "core_id": core_id,
                    "merged_basic_ids": merged_ids,
                    "cluster_size": len(cluster_ids),
                },
                memory_ids=[core_id] + merged_ids,
            )
            merged_source = merge_sources([
                getattr(core_node, "source", None),
                *(
                    getattr(basic_map.get(node_id), "source", None)
                    for node_id in cluster_ids
                ),
            ])
            if core_node is not None and merged_source != core_node.source:
                await self.graph_store.update_node(core_id, {"source": merged_source})

            logger.debug(f"[sweeper] Merged {len(merged_ids)} basics → existing core {core_id[:12]}")
            return 0, 1, []

        # 新建 Core
        patterns_text = ""
        for i, nid in enumerate(cluster_ids):
            node = basic_map.get(nid)
            content = node.content if node else nid
            patterns_text += f"Schema {chr(65+i)}: {content}\n"

        try:
            prompt = _CROSS_DOMAIN_INDUCTION_PROMPT.format(patterns=patterns_text.strip())
            llm_response = await self.llm_call(prompt)

            # Handle null response (LLM found no compelling connection)
            if not llm_response or llm_response.strip().lower() == "null":
                logger.info("[sweeper] LLM returned null — no compelling cross-domain pattern")
                return 0, 0, []

            # 兼容 LLM 返回 markdown code fence 包裹的 JSON
            _resp_text = llm_response.strip()
            if _resp_text.startswith("```"):
                _lines = _resp_text.split("\n")
                _lines = _lines[1:]
                if _lines and _lines[-1].strip() == "```":
                    _lines = _lines[:-1]
                _resp_text = "\n".join(_lines).strip()
            parsed = json.loads(_resp_text)
            core_content = parsed.get("core_pattern", "")
            reasoning = parsed.get("reasoning", "")
            confidence = parsed.get("confidence", 0.85)

            if not core_content:
                logger.warning("[sweeper] LLM returned empty core_pattern")
                return 0, 0, []

            # Embed core content
            core_embedding, core_embed_meta = await self.embed_service.embed_queued_with_meta(
                core_content
            )

            # 创建 L6 core 节点
            core_id = str(uuid.uuid4())
            core_node = MemoryNode(
                node_id=core_id,
                user_id=self.user_id,
                agent_id=self.agent_id,
                source=merge_sources(
                    getattr(basic_map.get(node_id), "source", None)
                    for node_id in cluster_ids
                ),
                layer=MemoryLayer.L6_SCHEMA,
                content=core_content,
                confidence=confidence,
                status=MemoryStatus.ACTIVE,
                source_type=SourceType.INFERRED,
                memory_at=datetime.now(),
                custom={"schema_type": "abstract", "reasoning": reasoning},
            )
            if core_embedding:
                core_node._graph_embedding = core_embedding
            await self.graph_store.upsert_memory_node(core_node)

            # Trace: core 创建记 memory_operation
            await self._log_mem_op(
                op="SWEEPER_CORE_CREATE",
                memory_id=core_id,
                content=json.dumps({
                    "core_content": core_content,
                    "confidence": confidence,
                    "source_basics": cluster_ids,
                }, ensure_ascii=False),
                reason=f"cross-domain induction from {len(cluster_ids)} basics",
            )

            # 创建 CROSS_ABSTRACTS_TO 边 + 继承 evidence
            inherited_evidence_ids: set = set()
            for nid in cluster_ids:
                await self.graph_store.add_cross_abstracts_to(nid, core_id)

                # 收集源节点的 evidence VdbRef（用于继承）
                try:
                    evidence_refs = await self.graph_store.get_evidence_vdbrefs(nid)
                    for ref in evidence_refs:
                        vid = ref.get("node_id") or ref.get("vdb_id", "")
                        if vid:
                            inherited_evidence_ids.add(vid)
                except Exception as e:
                    logger.debug(f"[sweeper] get_evidence for {nid[:12]} failed: {e}")

                # Trace: 每条边记 memory_operation（两端 node_id 都可追溯）
                await self._log_mem_op(
                    op="SWEEPER_CROSS_EDGE",
                    memory_id=nid,
                    content=json.dumps({"basic_id": nid, "core_id": core_id}, ensure_ascii=False),
                    reason=f"basic → core {core_id[:12]}",
                )

            # Trace: pipeline_log 记 LLM prompt/response + 完整上下文
            await self._log_pipeline(
                step="SWEEPER_CORE_CREATE",
                prompt=prompt,
                response=llm_response,
                parsed={
                    "core_id": core_id,
                    "core_content": core_content,
                    "confidence": confidence,
                    "source_basic_ids": cluster_ids,
                    "provider_meta": core_embed_meta,
                },
                memory_ids=[core_id] + cluster_ids,
            )

            # 继承源节点的 evidence：为 core 建立 DERIVED_FROM 边到所有源的 VdbRef
            if inherited_evidence_ids:
                for vid in inherited_evidence_ids:
                    try:
                        await self.graph_store.add_derived_from(core_id, vid)
                    except Exception as e:
                        logger.debug(f"[sweeper] inherit evidence {vid[:12]} → core {core_id[:12]} failed: {e}")
                logger.debug(
                    f"[sweeper] Inherited {len(inherited_evidence_ids)} evidence VdbRefs → core {core_id[:12]}"
                )

            logger.info(
                f"[sweeper] Created L6 core {core_id[:12]} "
                f"from {len(cluster_ids)} basics, evidence_inherited={len(inherited_evidence_ids)}"
            )
            logger.debug(f"[sweeper] L6 core {core_id[:12]} content={core_content[:60]!r}")
            return 1, 0, [core_id]

        except Exception as e:
            logger.error(f"[sweeper] Core creation failed: {e}")
            return 0, 0, []
