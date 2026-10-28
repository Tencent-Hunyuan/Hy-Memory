"""
HY Memory - System 2 Writer (ultra 模式)

写入流程:
  System 1 (同步): 复用 MemoryWriter 的 write 逻辑（含意图抽取 → L7_INTENTION，
                   见 extractor + writer._store_intentions；意图属于 S1/pro，不是 S2）。
                   write 只跑 S1 即返回。
  System 2 (显式): 由 System 2 Agent（LLM + Graph tools）对 VDB 全量做认知加工，
                   归纳心智模型 → L6_SCHEMA，再跑 cross-domain sweeper。
                   Agent 是 Graph 的唯一写入者。

触发时机由调用方（App 层）通过 evolve() 显式控制：
  - write 不入队、不自动触发；SDK 不维护进程内 S2 队列（重启丢任务、多 Pod
    各自一套队列、与 App 持久化 write 队列双轨等问题由此消除）。
  - evolve() 一次 = 对该 user 的 VDB 全量跑一次 S2 + sweeper（同步阻塞、非幂等）。
"""

import os
from typing import Optional, Dict, Any
from datetime import datetime
import logging

from .base import WritePipeline, WriteRequest, WriteResponse, PipelineContext
from .writer import MemoryWriter
from ..config import MemoryConfig
from ..core.embed_service import EmbedService
from ..data.vector_store_base import VectorStoreBase
from ..data.graph_store_base import GraphStoreBase
from ..models.memory import MemoryNode
from ..utils.tracer import PipelineTracer, create_tracer

import time
logger = logging.getLogger(__name__)

_SWEEPER_ENABLED = os.getenv("SWEEPER_ENABLED", "true").lower() == "true"


# ================================================================
# System 2 Trigger Mode
# ================================================================

# ================================================================
# Pro Write Pipeline
# ================================================================

class System2Writer(WritePipeline):
    """
    System 2 写入器 (ultra 模式)

    特点:
    - System 1 同步路径完全复用 MemoryWriter；write 只跑 S1 即返回。
    - System 2 不在 write 里自动触发、不维护进程内队列。触发时机完全由调用方
      （App 层）通过显式调用 evolve() 控制 —— 避免 SDK 内存队列重启丢任务、
      多 Pod 各自一套队列、与 App 持久化 write 队列双轨等问题。
    - evolve() 一次 = 对该 user 的 VDB 全量跑一次 S2 Agent + sweeper（非幂等）。
    """

    VERSION = "system2"

    def __init__(
        self,
        config: MemoryConfig,
        embed_service: Optional[EmbedService] = None,
        vector_store: Optional[VectorStoreBase] = None,
        graph_store: Optional[GraphStoreBase] = None,
        cache=None,
    ):
        self.config = config
        self._embed_service = embed_service
        self._vector_store = vector_store
        self._graph_store = graph_store
        self._cache = cache

        # System 1: 复用 MemoryWriter
        self._writer: Optional[MemoryWriter] = None

        self._initialized = False

    async def initialize(self) -> None:
        """初始化 Pro Pipeline"""
        if self._initialized:
            return

        # 初始化 MemoryWriter (System 1)
        self._writer = MemoryWriter(
            config=self.config,
            embed_service=self._embed_service,
            vector_store=self._vector_store,
            cache=self._cache,
            graph_store=self._graph_store,
        )
        await self._writer.initialize()

        self._initialized = True
        logger.info("System2Writer initialized (S2 triggered explicitly via evolve)")

    async def write(
        self,
        request: WriteRequest,
        ctx: Optional[PipelineContext] = None,
        tracer: Optional[PipelineTracer] = None,
    ) -> WriteResponse:
        """
        执行 Pro 写入流程:
        1. System 1 同步写入 (复用 Lite)，写完即返回。
        2. System 2 不在此触发 —— 由调用方显式调用 evolve()。
        """
        start_time = datetime.now()

        # 创建 tracer
        if tracer is None:
            tracer = create_tracer(
                operation="system2_write",
                pipeline_version="system2",
                uid=request.user_id,
                agent_id=request.agent_id,
                request_id=request.request_id,
                content_preview=request.content,
            )

        # 确保已初始化
        if not self._initialized:
            await self.initialize()

        # ============================================
        # System 1: 复用 Lite 的完整写入流程
        # ============================================
        with tracer.span("system1_write") as s:
            response = await self._writer.write(request, ctx=ctx)
            s.set_output({
                "success": response.success,
                "memory_id": response.memory_id,
                "layer": response.layer,
            })

        if not response.success:
            response.elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
            return response

        # System 2 由调用方通过 evolve() 显式触发，write 不入队、不自动跑。
        response.extra["pipeline_version"] = "system2"
        response.extra["system2_task_ids"] = []

        response.elapsed_ms = (datetime.now() - start_time).total_seconds() * 1000
        return response

    async def write_extracted(
        self,
        request: WriteRequest,
        ctx: Optional[PipelineContext] = None,
        tracer: Optional[PipelineTracer] = None,
    ) -> WriteResponse:
        """已抽取事实直写：委托给内部 MemoryWriter.write_extracted（跳过 extract）。

        System 2 不在此触发——与 write() 一致，认知加工由 evolve() 显式触发。
        """
        if not self._initialized:
            await self.initialize()
        response = await self._writer.write_extracted(request, ctx=ctx, tracer=tracer)
        response.extra["pipeline_version"] = "system2"
        response.extra["system2_task_ids"] = []
        return response

    # ================================================================
    # System 2 认知加工
    # ================================================================

    async def _run_system2_workers(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        System 2 认知加工 — System 2 Agent + Graph

        全面使用 Agent（LLM + 8 tools）替代旧的独立 Workers。
        Agent 是 Graph 的唯一写入者。
        """
        return await self._run_system2_agent(payload)

    async def _run_system2_agent_with_timing(self, payload: Dict[str, Any]) -> Dict[str, float]:
        """
        包裹 _run_system2_agent，返回细分 timing:
          - preprocess_ms
          - agent_generate_ms（纯 LLM 时间，= agent elapsed - tools 总耗时）
          - tools_avg_ms（tool 平均执行耗时）
        """
        from .system2_agent import prepare_materials, run_system2_agent, s2_agent_skip_reason
        from .system2_tools import System2ToolExecutor

        user_id = payload["user_id"]
        agent_id = payload["agent_id"]
        request_id = payload.get("request_id", "")

        vector_store = await self._writer._get_vector_store()
        embed_service = self._writer.embed_service

        # Phase 1: preprocess
        _t = time.time()
        materials = await prepare_materials(
            user_id=user_id,
            agent_id=agent_id,
            vector_store=vector_store,
            graph_store=self._graph_store,
            embed_service=embed_service,
            config=self.config,
        )
        preprocess_ms = (time.time() - _t) * 1000

        # Log preprocessing
        await self._store_system2_log(
            request_id=request_id,
            user_id=user_id,
            agent_id=agent_id,
            step="SYSTEM2_PREPROCESS",
            result={
                "success": True,
                **materials.get("stats", {}),
                "clusters": materials.get("clusters", []),
                "graph_forward": materials.get("graph_forward", []),
                "graph_reverse": materials.get("graph_reverse", []),
                "unprocessed_facts": materials.get("unprocessed_facts", []),
            },
        )

        skip_reason = s2_agent_skip_reason(materials)
        if skip_reason:
            stats = materials.get("stats", {})
            logger.info(
                f"[S2-agent] skipping agent: {skip_reason} "
                f"(pool={stats.get('total_facts_pool', 0)} "
                f"clusters={stats.get('clusters_found', 0)} "
                f"fresh={stats.get('fresh_facts', 0)})"
            )
            return {"preprocess_ms": preprocess_ms, "agent_generate_ms": 0, "tools_avg_ms": 0}

        # Phase 2: Agent
        tool_executor = System2ToolExecutor(
            vector_store=vector_store,
            graph_store=self._graph_store,
            embed_service=embed_service,
            user_id=user_id,
            agent_id=agent_id,
        )

        agent_result = await run_system2_agent(
            materials=materials,
            tool_executor=tool_executor,
            config=self.config,
        )

        # 计算 timing
        agent_elapsed_ms = agent_result.get("elapsed_ms", 0)
        tool_call_log = agent_result.get("tool_call_log", [])
        tools_total_ms = sum(t.get("elapsed_ms", 0) for t in tool_call_log)
        tools_count = len(tool_call_log)
        agent_generate_ms = max(0, agent_elapsed_ms - tools_total_ms)
        tools_avg_ms = tools_total_ms / tools_count if tools_count > 0 else 0

        # Log agent result
        await self._store_system2_log(
            request_id=request_id,
            user_id=user_id,
            agent_id=agent_id,
            step="SYSTEM2_AGENT",
            result={
                "success": agent_result.get("success", False),
                "tool_calls_count": len(agent_result.get("tool_calls", [])),
                "iterations": agent_result.get("iterations", 0),
                "total_tokens": agent_result.get("total_tokens", 0),
                "elapsed_ms": agent_elapsed_ms,
                "agent_generate_ms": round(agent_generate_ms, 1),
                "tools_avg_ms": round(tools_avg_ms, 1),
            },
        )

        # Step 2: SYSTEM2_AGENT_TRAJECTORY — 完整 tool_calls
        import json as _json_s2_t
        tool_calls = agent_result.get("tool_calls", [])
        if self._cache and tool_calls:
            try:
                await self._cache.store_pipeline_log(
                    request_id=request_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    step="SYSTEM2_AGENT_TRAJECTORY",
                    prompt="",
                    response=_json_s2_t.dumps({
                        "tool_calls": tool_calls,
                        "agent_reasoning": agent_result.get("agent_reasoning", ""),
                    }, ensure_ascii=False, default=str),
                    parsed=_json_s2_t.dumps({
                        "tool_calls": tool_calls,
                        "tool_calls_count": len(tool_calls),
                        "iterations": agent_result.get("iterations", 0),
                        "has_reasoning": bool(agent_result.get("agent_reasoning")),
                        "total_tokens": agent_result.get("total_tokens", 0),
                    }, ensure_ascii=False, default=str),
                )
            except Exception as e:
                logger.debug(f"[S2] store SYSTEM2_AGENT_TRAJECTORY failed: {e}")

        # Step 3: SYSTEM2_AGENT_MESSAGES — 完整 LLM 对话历史
        messages = agent_result.get("messages")
        if messages and self._cache:
            try:
                clean_messages = []
                for m in messages:
                    clean_msg = {"role": m.get("role", "")}
                    if m.get("content"):
                        clean_msg["content"] = m["content"]
                    if m.get("tool_calls"):
                        clean_msg["tool_calls"] = m["tool_calls"]
                    if m.get("tool_call_id"):
                        clean_msg["tool_call_id"] = m["tool_call_id"]
                    clean_messages.append(clean_msg)

                await self._cache.store_pipeline_log(
                    request_id=request_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    step="SYSTEM2_AGENT_MESSAGES",
                    prompt=clean_messages[1]["content"] if len(clean_messages) > 1 else "",
                    response=_json_s2_t.dumps(clean_messages, ensure_ascii=False, default=str),
                    parsed=_json_s2_t.dumps({
                        "message_count": len(clean_messages),
                        "roles": [m["role"] for m in clean_messages],
                    }, ensure_ascii=False, default=str),
                )
            except Exception as e:
                logger.debug(f"[S2] store SYSTEM2_AGENT_MESSAGES failed: {e}")

        return {
            "preprocess_ms": preprocess_ms,
            "agent_generate_ms": agent_generate_ms,
            "tools_avg_ms": tools_avg_ms,
        }

    async def _run_system2_agent(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        System 2 Agent — 预处理 + LLM Agent + Graph tools
        """
        from .system2_agent import (
            build_evoling_batches,
            cluster_fingerprint,
            prepare_materials,
            prepare_holistic_therapy_materials,
            profile_fact_promotion_enabled,
            run_profile_fact_promotion,
            run_canonical_state_projection,
            run_forget_retirement,
            run_system2_agent,
            s2_agent_skip_reason,
        )
        from .balanced_catalog import (
            balanced_catalog_generation_enabled,
            run_balanced_catalog_generation,
        )
        from .specific_supplement import (
            run_specific_supplement_generation,
            specific_supplement_generation_enabled,
        )
        from .system2_tools import System2ToolExecutor, run_existing_schema_compaction

        user_id = payload["user_id"]
        agent_id = payload["agent_id"]
        request_id = payload.get("request_id", "")

        vector_store = await self._writer._get_vector_store()
        embed_service = self._writer.embed_service

        # Phase 1: 预处理
        materials = await prepare_materials(
            user_id=user_id,
            agent_id=agent_id,
            vector_store=vector_store,
            graph_store=self._graph_store,
            embed_service=embed_service,
            config=self.config,
        )

        # Log preprocessing
        await self._store_system2_log(
            request_id=request_id,
            user_id=user_id,
            agent_id=agent_id,
            step="SYSTEM2_PREPROCESS",
            result={
                "success": True,
                **materials.get("stats", {}),
                "clusters": materials.get("clusters", []),
                "graph_forward": materials.get("graph_forward", []),
                "graph_reverse": materials.get("graph_reverse", []),
                "unprocessed_facts": materials.get("unprocessed_facts", []),
            },
        )

        tool_executor = System2ToolExecutor(
            vector_store=vector_store,
            graph_store=self._graph_store,
            embed_service=embed_service,
            user_id=user_id,
            agent_id=agent_id,
        )

        canonical_state = await run_canonical_state_projection(
            reviews=list(materials.get("canonical_state_reviews") or []),
            tool_executor=tool_executor,
        )
        if not canonical_state.get("success"):
            return {
                "system2_agent": {
                    "success": False,
                    "cycle_status": "partial",
                    "partial": True,
                    "error": "canonical state projection failed",
                    "canonical_state": canonical_state,
                    "should_continue": True,
                    "continuation_reason": "canonical_state_failed",
                },
                "should_continue": True,
                "continuation_reason": "canonical_state_failed",
            }
        if int(canonical_state.get("created") or 0) > 0:
            # Let ordinary induction see the newly-created explicit state in the
            # same evoling cycle, so it can add evidence instead of duplicating it.
            materials = await prepare_materials(
                user_id=user_id,
                agent_id=agent_id,
                vector_store=vector_store,
                graph_store=self._graph_store,
                embed_service=embed_service,
                config=self.config,
            )

        holistic_probe = await prepare_holistic_therapy_materials(
            user_id=user_id,
            agent_id=agent_id,
            vector_store=vector_store,
            graph_store=self._graph_store,
        )

        skip_reason = s2_agent_skip_reason(materials)
        if (
            skip_reason and not materials.get("forget_markers")
            and not holistic_probe.get("pending")
            and not profile_fact_promotion_enabled()
            and not balanced_catalog_generation_enabled()
            and not specific_supplement_generation_enabled()
        ):
            stats = materials.get("stats", {})
            logger.info(
                f"[S2-agent] skipping agent: {skip_reason} "
                f"(pool={stats.get('total_facts_pool', 0)} "
                f"clusters={stats.get('clusters_found', 0)})"
            )
            return {
                "system2_agent": {
                    "success": True,
                    "skipped": True,
                    "reason": skip_reason,
                    "cycle_status": "complete",
                    "partial": False,
                    "batches_planned": 0,
                    "batches_completed": 0,
                    "batches_failed": 0,
                    "batch_failures": [],
                    "should_continue": False,
                    "continuation_reason": skip_reason,
                    "remaining_clusters": int(stats.get("clusters_found") or 0),
                    "remaining_actionable_clusters": 0,
                    "remaining_fresh_facts": int(stats.get("fresh_facts") or 0),
                    "canonical_state": canonical_state,
                },
                "should_continue": False,
                "continuation_reason": skip_reason,
                "remaining_clusters": int(stats.get("clusters_found") or 0),
                "remaining_actionable_clusters": 0,
                "remaining_fresh_facts": int(stats.get("fresh_facts") or 0),
            }

        # Phase 2: bounded evoling batches. One cluster per LLM call prevents a
        # large user's operation array from being truncated at max_tokens.
        max_batches = max(1, int(
            payload.get("max_batches")
            or os.getenv("MEMORY_EVOLING_MAX_BATCHES", "128")
        ))
        batch_results = []
        all_tool_calls = list(canonical_state.get("tool_calls") or [])
        all_tool_logs = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        total_elapsed_ms = 0.0
        failed_batches = 0
        no_actionable_fingerprints = set()
        current_materials = materials
        initial_clusters = int((materials.get("stats") or {}).get("clusters_found") or 0)
        for batch_index in range(max_batches):
            actionable_clusters = [
                cluster for cluster in current_materials.get("clusters") or []
                if cluster_fingerprint(cluster) not in no_actionable_fingerprints
            ]
            if not actionable_clusters:
                break
            batch = build_evoling_batches(
                {**current_materials, "clusters": actionable_clusters},
                max_batches=1,
            )[0]
            cluster = batch["clusters"][0]
            fingerprint = cluster_fingerprint(cluster)
            batch_result = await run_system2_agent(
                materials=batch,
                tool_executor=tool_executor,
                config=self.config,
            )
            batch_record = {
                "batch_index": batch_index,
                "cluster_ids": [
                    fact.get("node_id")
                    for cluster in batch.get("clusters", [])
                    for fact in cluster.get("facts", [])
                ],
                "cluster_fingerprint": fingerprint,
                **batch_result,
            }
            batch_results.append(batch_record)
            all_tool_calls.extend(batch_result.get("tool_calls") or [])
            all_tool_logs.extend(batch_result.get("tool_call_log") or [])
            total_prompt_tokens += int(batch_result.get("total_prompt_tokens") or 0)
            total_completion_tokens += int(batch_result.get("total_completion_tokens") or 0)
            total_elapsed_ms += float(batch_result.get("elapsed_ms") or 0.0)
            if int(batch_result.get("state_change_count") or 0) == 0:
                no_actionable_fingerprints.add(fingerprint)
            if batch_result.get("success"):
                handled_ids = batch_result.get("handled_candidate_ids")
                await self._mark_batch_reviewed(
                    batch, vector_store,
                    handled_node_ids=set(handled_ids) if handled_ids is not None else None,
                )
            # Re-scan after every attempt. A batch may persist an early operation
            # and then fail on a later operation; those durable changes are the
            # checkpoint from which a continuation must resume.
            current_materials = await prepare_materials(
                user_id=user_id,
                agent_id=agent_id,
                vector_store=vector_store,
                graph_store=self._graph_store,
                embed_service=embed_service,
                config=self.config,
            )
            if not batch_result.get("success"):
                failed_batches += 1
                break

        # Phase 3: explicit forgetting is processed after ordinary induction.
        # This ordering handles full-history first runs where a positive fact and
        # its later forget marker are both fresh: a transient Schema may be
        # created in Phase 2, then immediately archived before the cycle returns.
        forget_result = await run_forget_retirement(
            forget_markers=list(materials.get("forget_markers") or []),
            graph_store=self._graph_store,
            tool_executor=tool_executor,
            config=self.config,
            isolation_key=MemoryNode.build_isolation_key(user_id, agent_id or "default"),
        )
        forget_failed = not bool(forget_result.get("success"))
        if not forget_failed:
            resolved_marker_ids = set(forget_result.get("resolved_marker_ids") or [])
            for marker in materials.get("forget_markers") or []:
                marker_id = marker.get("node_id")
                if not marker_id or marker_id not in resolved_marker_ids:
                    continue
                try:
                    node = await vector_store.get_by_id(marker_id)
                    if node is None:
                        continue
                    custom = dict(node.custom or {})
                    custom["s2_forget_reviewed"] = True
                    await vector_store.update_payload(marker_id, {"custom": custom})
                except Exception as exc:
                    logger.warning("[S2] mark forget reviewed failed node_id=%s: %s", marker_id, exc)
        all_tool_calls.extend(forget_result.get("tool_calls") or [])
        all_tool_logs.extend(tool_executor.tool_call_log[len(all_tool_logs):])
        total_prompt_tokens += int(forget_result.get("prompt_tokens") or 0)
        total_completion_tokens += int(forget_result.get("completion_tokens") or 0)

        # Refresh after checkpointing resolved markers so they do not re-enter
        # the ordinary fresh-fact pool on the next cycle and remaining counts
        # reflect the state returned to the caller.
        if materials.get("forget_markers"):
            current_materials = await prepare_materials(
                user_id=user_id,
                agent_id=agent_id,
                vector_store=vector_store,
                graph_store=self._graph_store,
                embed_service=embed_service,
                config=self.config,
            )

        holistic_materials = holistic_probe
        holistic_result = {
            "enabled": bool(holistic_probe.get("enabled")),
            "skipped": True,
            "reason": "not_pending" if holistic_probe.get("enabled") else "disabled",
            "success": True,
            "tool_calls": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }

        # current_materials is the checkpoint after the last successful batch.
        remaining = current_materials
        remaining_stats = remaining.get("stats") or {}
        remaining_clusters = int(remaining_stats.get("clusters_found") or 0)
        remaining_fingerprints = {
            cluster_fingerprint(cluster)
            for cluster in remaining.get("clusters") or []
        }
        actionable_remaining = remaining_fingerprints - no_actionable_fingerprints
        if (
            holistic_probe.get("pending")
            and failed_batches == 0
            and not forget_failed
            and not actionable_remaining
        ):
            # Ordinary induction may have created or revised Schemas. Refresh
            # the holistic view so the global pass can reuse those nodes rather
            # than reasoning against the stale pre-cycle Graph snapshot.
            holistic_materials = await prepare_holistic_therapy_materials(
                user_id=user_id,
                agent_id=agent_id,
                vector_store=vector_store,
                graph_store=self._graph_store,
            )
            holistic_result = await run_system2_agent(
                materials=holistic_materials,
                tool_executor=tool_executor,
                config=self.config,
            )
            if holistic_result.get("success"):
                await self._mark_holistic_therapy_reviewed(
                    holistic_materials, vector_store,
                    excluded_node_ids=set(
                        holistic_result.get("rejected_candidate_ids") or []
                    ),
                )
            all_tool_calls.extend(holistic_result.get("tool_calls") or [])
            all_tool_logs.extend(holistic_result.get("tool_call_log") or [])
            total_prompt_tokens += int(holistic_result.get("total_prompt_tokens") or 0)
            total_completion_tokens += int(holistic_result.get("total_completion_tokens") or 0)
            total_elapsed_ms += float(holistic_result.get("elapsed_ms") or 0.0)
        holistic_failed = not bool(holistic_result.get("success"))
        if holistic_result.get("success") and holistic_materials.get("pending"):
            holistic_after = await prepare_holistic_therapy_materials(
                user_id=user_id,
                agent_id=agent_id,
                vector_store=vector_store,
                graph_store=self._graph_store,
            )
            holistic_remaining = int(
                (holistic_after.get("stats") or {}).get("holistic_pending_facts", 0)
            )
        elif holistic_result.get("success"):
            holistic_remaining = 0
        else:
            holistic_remaining = int(
                (holistic_probe.get("stats") or {}).get("holistic_pending_facts", 0)
            )
        profile_promotion = {
            "success": True, "enabled": False, "selected": [], "reviewed": 0,
            "remaining": 0, "prompt_tokens": 0, "completion_tokens": 0,
        }
        if (
            failed_batches == 0 and not forget_failed and not holistic_failed
            and not actionable_remaining
        ):
            profile_promotion = await run_profile_fact_promotion(
                user_id=user_id,
                agent_id=agent_id,
                vector_store=vector_store,
                config=self.config,
                forget_markers=list(materials.get("forget_markers") or []),
            )
            all_tool_calls.extend({
                "tool": "promote_fact", "args": item,
                "result": {"promoted": True},
            } for item in profile_promotion.get("selected") or [])
            total_prompt_tokens += int(profile_promotion.get("prompt_tokens") or 0)
            total_completion_tokens += int(profile_promotion.get("completion_tokens") or 0)
        profile_promotion_failed = not bool(profile_promotion.get("success"))
        profile_promotion_remaining = int(profile_promotion.get("remaining") or 0)
        schema_compaction = {
            "success": True, "enabled": False, "scanned": 0, "merged": 0, "merges": [],
        }
        if (
            failed_batches == 0 and not forget_failed and not holistic_failed
            and not actionable_remaining and holistic_remaining == 0
            and not profile_promotion_failed and profile_promotion_remaining == 0
        ):
            schema_compaction = await run_existing_schema_compaction(
                graph_store=self._graph_store,
                tool_executor=tool_executor,
                embed_service=embed_service,
                isolation_key=MemoryNode.build_isolation_key(
                    user_id, agent_id or "default",
                ),
            )
        schema_compaction_failed = not bool(schema_compaction.get("success"))
        balanced_catalog = {
            "success": True,
            "enabled": False,
            "created": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        if (
            failed_batches == 0 and not forget_failed and not holistic_failed
            and not actionable_remaining and holistic_remaining == 0
            and not profile_promotion_failed and profile_promotion_remaining == 0
            and not schema_compaction_failed
        ):
            balanced_catalog = await run_balanced_catalog_generation(
                user_id=user_id,
                vector_store=vector_store,
                graph_store=self._graph_store,
                config=self.config,
            )
            total_prompt_tokens += int(balanced_catalog.get("prompt_tokens") or 0)
            total_completion_tokens += int(balanced_catalog.get("completion_tokens") or 0)
        balanced_catalog_failed = not bool(balanced_catalog.get("success"))
        specific_supplement = {
            "success": True,
            "enabled": False,
            "created": [],
            "prompt_tokens": 0,
            "completion_tokens": 0,
        }
        if (
            failed_batches == 0 and not forget_failed and not holistic_failed
            and not actionable_remaining and holistic_remaining == 0
            and not profile_promotion_failed and profile_promotion_remaining == 0
            and not schema_compaction_failed and not balanced_catalog_failed
        ):
            specific_supplement = await run_specific_supplement_generation(
                user_id=user_id,
                vector_store=vector_store,
                graph_store=self._graph_store,
                config=self.config,
            )
            total_prompt_tokens += int(
                specific_supplement.get("prompt_tokens") or 0
            )
            total_completion_tokens += int(
                specific_supplement.get("completion_tokens") or 0
            )
        specific_supplement_failed = not bool(
            specific_supplement.get("success")
        )
        initial_clusters = int((materials.get("stats") or {}).get("clusters_found") or 0)
        budget_exhausted = (
            len(batch_results) >= max_batches and bool(actionable_remaining)
        )
        unresolved_forget = list(forget_result.get("unresolved_marker_ids") or [])
        should_continue = bool(
            failed_batches or forget_failed or holistic_failed or budget_exhausted
            or profile_promotion_failed or schema_compaction_failed
            or balanced_catalog_failed or specific_supplement_failed
            or actionable_remaining or unresolved_forget
            or holistic_remaining or profile_promotion_remaining
        )
        reasons = []
        if failed_batches:
            reasons.append("batch_failed")
        if forget_failed:
            reasons.append("forget_retirement_failed")
        if holistic_failed:
            reasons.append("holistic_therapy_failed")
        if profile_promotion_failed:
            reasons.append("profile_fact_promotion_failed")
        if schema_compaction_failed:
            reasons.append("schema_compaction_failed")
        if balanced_catalog_failed:
            reasons.append("balanced_catalog_failed")
        if specific_supplement_failed:
            reasons.append("specific_supplement_failed")
        if budget_exhausted:
            reasons.append("batch_budget_exhausted")
        if actionable_remaining:
            reasons.append("clusters_remaining")
        if unresolved_forget:
            reasons.append("forget_markers_unresolved")
        if holistic_remaining:
            reasons.append("holistic_therapy_remaining")
        if profile_promotion_remaining:
            reasons.append("profile_fact_promotion_remaining")
        if remaining_clusters and not actionable_remaining and not failed_batches and not budget_exhausted:
            reasons.append("no_actionable_progress")

        agent_result = {
            # A failed LLM batch is a real task failure. The caller/queue may
            # retry the same evoling request; the re-scan above makes that
            # retry resume from the durable graph checkpoint.
            "success": (
                failed_batches == 0 and not forget_failed and not holistic_failed
                and not profile_promotion_failed and not schema_compaction_failed
                and not balanced_catalog_failed and not specific_supplement_failed
            ),
            "cycle_status": "partial" if should_continue else "complete",
            "partial": should_continue,
            "batches_planned": min(initial_clusters, max_batches),
            "batches_completed": len(batch_results) - failed_batches,
            "batches_failed": failed_batches,
            "batch_failures": [
                {
                    "batch_index": batch.get("batch_index"),
                    "cluster_fingerprint": batch.get("cluster_fingerprint"),
                    "error_code": batch.get("error_code"),
                    "failure_kind": batch.get("failure_kind"),
                    "error": batch.get("error"),
                    "truncated": bool(batch.get("truncated")),
                }
                for batch in batch_results if not batch.get("success")
            ],
            "batch_results": batch_results,
            "forget_retirement": forget_result,
            "canonical_state": canonical_state,
            "balanced_catalog": balanced_catalog,
            "specific_supplement": specific_supplement,
            "holistic_therapy": holistic_result,
            "profile_fact_promotion": profile_promotion,
            "schema_compaction": schema_compaction,
            "tool_calls": all_tool_calls,
            "tool_call_log": all_tool_logs,
            "total_prompt_tokens": total_prompt_tokens,
            "total_completion_tokens": total_completion_tokens,
            "total_tokens": total_prompt_tokens + total_completion_tokens,
            "elapsed_ms": total_elapsed_ms,
            "iterations": len(batch_results),
            "should_continue": should_continue,
            "continuation_reason": ",".join(reasons) if reasons else "complete",
            "remaining_clusters": remaining_clusters,
            "remaining_actionable_clusters": len(actionable_remaining),
            "remaining_fresh_facts": int(remaining_stats.get("fresh_facts") or 0),
            "remaining_forget_markers": len(unresolved_forget),
            "remaining_holistic_therapy_facts": holistic_remaining,
        }

        # Log agent result — 完整 trajectory
        import json as _json_s2

        # Step 1: SYSTEM2_AGENT 主日志（聚合统计）
        await self._store_system2_log(
            request_id=request_id,
            user_id=user_id,
            agent_id=agent_id,
            step="SYSTEM2_AGENT",
            result={
                "success": agent_result.get("success", False),
                "tool_calls_count": len(agent_result.get("tool_calls", [])),
                "iterations": agent_result.get("iterations", 0),
                "total_tokens": agent_result.get("total_tokens", 0),
                "prompt_tokens": agent_result.get("total_prompt_tokens", 0),
                "completion_tokens": agent_result.get("total_completion_tokens", 0),
                "elapsed_ms": agent_result.get("elapsed_ms", 0),
            },
        )

        # Step 2: SYSTEM2_AGENT_TRAJECTORY — 完整 trajectory（tool_calls + reasoning）
        # 无条件存储：即使没有 tool calls，Agent 的推理过程本身就是 trajectory
        tool_calls = agent_result.get("tool_calls", [])
        if self._cache:
            try:
                await self._cache.store_pipeline_log(
                    request_id=request_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    step="SYSTEM2_AGENT_TRAJECTORY",
                    prompt="",
                    response=_json_s2.dumps({
                        "tool_calls": tool_calls,
                        "agent_reasoning": agent_result.get("agent_reasoning", ""),
                    }, ensure_ascii=False, default=str),
                    parsed=_json_s2.dumps({
                        "tool_calls": tool_calls,
                        "tool_calls_count": len(tool_calls),
                        "iterations": agent_result.get("iterations", 0),
                        "has_reasoning": bool(agent_result.get("agent_reasoning")),
                        "total_tokens": agent_result.get("total_tokens", 0),
                    }, ensure_ascii=False, default=str),
                )
            except Exception as e:
                logger.debug(f"[S2] store SYSTEM2_AGENT_TRAJECTORY failed: {e}")

        # Step 3: SYSTEM2_AGENT_MESSAGES — 完整 LLM 对话历史（可重放）
        messages = agent_result.get("messages")
        if messages and self._cache:
            try:
                # 过滤掉 embedding 等大字段，只保留文本
                clean_messages = []
                for m in messages:
                    clean_msg = {"role": m.get("role", "")}
                    if m.get("content"):
                        clean_msg["content"] = m["content"]
                    if m.get("tool_calls"):
                        clean_msg["tool_calls"] = m["tool_calls"]
                    if m.get("tool_call_id"):
                        clean_msg["tool_call_id"] = m["tool_call_id"]
                    clean_messages.append(clean_msg)

                await self._cache.store_pipeline_log(
                    request_id=request_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    step="SYSTEM2_AGENT_MESSAGES",
                    prompt=clean_messages[1]["content"] if len(clean_messages) > 1 else "",  # user msg (materials)
                    response=_json_s2.dumps(clean_messages, ensure_ascii=False, default=str),
                    parsed=_json_s2.dumps({
                        "message_count": len(clean_messages),
                        "roles": [m["role"] for m in clean_messages],
                    }, ensure_ascii=False, default=str),
                )
            except Exception as e:
                logger.debug(f"[S2] store SYSTEM2_AGENT_MESSAGES failed: {e}")

        # Step 4: Graph 写入操作记 memory_operations（可追溯）
        tool_call_log = agent_result.get("tool_call_log", [])
        if tool_call_log and self._cache:
            for tc in tool_call_log:
                tool_name = tc.get("tool", "")
                if tool_name in ("create_graph_node", "update_graph_node", "delete_graph_node",
                                 "revise_schema", "retire_schema", "add_edge"):
                    try:
                        result_preview = tc.get("result_preview", "")
                        # 尝试提取 node_id
                        try:
                            result_data = _json_s2.loads(result_preview) if result_preview else {}
                        except Exception:
                            result_data = {}
                        memory_id = result_data.get("node_id", "")

                        op_name = {
                            "create_graph_node": "GRAPH_CREATE",
                            "update_graph_node": "GRAPH_UPDATE",
                            "delete_graph_node": "GRAPH_DELETE",
                            "add_edge": "GRAPH_ADD_EDGE",
                            "revise_schema": "GRAPH_REVISE",
                            "retire_schema": "GRAPH_RETIRE",
                        }.get(tool_name, "GRAPH_OP")

                        await self._cache.store_memory_operation(
                            request_id=request_id,
                            user_id=user_id,
                            agent_id=agent_id,
                            op=op_name,
                            memory_id=memory_id,
                            content=_json_s2.dumps(tc.get("args", {}), ensure_ascii=False, default=str)[:500],
                            layer="graph",
                            reason=f"System 2 Agent tool: {tool_name}",
                        )
                    except Exception as e:
                        logger.debug(f"[S2] store graph memory_operation failed: {e}")

        return {
            "system2_agent": agent_result,
            "success": agent_result["success"],
            "should_continue": should_continue,
            "continuation_reason": agent_result["continuation_reason"],
            "remaining_clusters": remaining_clusters,
            "remaining_actionable_clusters": len(actionable_remaining),
            "remaining_fresh_facts": agent_result["remaining_fresh_facts"],
        }

    async def _mark_batch_reviewed(
        self,
        batch: Dict[str, Any],
        vector_store,
        handled_node_ids: Optional[set[str]] = None,
    ) -> int:
        """Record a completed S2 review even when it intentionally emits no Schema."""
        from .system2_agent import fact_source_keys

        updated = 0
        for cluster in batch.get("clusters") or []:
            for fact in cluster.get("facts") or []:
                node_id = fact.get("node_id")
                if not node_id:
                    continue
                if handled_node_ids is not None and node_id not in handled_node_ids:
                    continue
                try:
                    node = await vector_store.get_by_id(node_id)
                    if node is None:
                        continue
                    custom = dict(node.custom or {})
                    custom["s2_reviewed_count"] = int(custom.get("s2_reviewed_count") or 0) + 1
                    # Freshness also counts a session when no raw provenance is
                    # present; use the same source definition for its checkpoint.
                    custom["s2_reviewed_source_count"] = len(fact_source_keys(node))
                    await vector_store.update_payload(node_id, {"custom": custom})
                    updated += 1
                except Exception as exc:
                    logger.warning("[S2] mark reviewed failed node_id=%s: %s", node_id, exc)
        return updated

    async def _mark_holistic_therapy_reviewed(
        self,
        materials: Dict[str, Any],
        vector_store,
        excluded_node_ids: Optional[set[str]] = None,
    ) -> int:
        """Checkpoint source counts consumed by the global therapy pass."""
        from .system2_agent import fact_source_keys

        updated = 0
        seen = set()
        for cluster in materials.get("clusters") or []:
            for fact in cluster.get("facts") or []:
                node_id = str(fact.get("node_id") or "")
                if not node_id or node_id in seen:
                    continue
                if excluded_node_ids and node_id in excluded_node_ids:
                    continue
                seen.add(node_id)
                try:
                    node = await vector_store.get_by_id(node_id)
                    if node is None:
                        continue
                    custom = dict(node.custom or {})
                    custom["s2_holistic_therapy_reviewed_source_count"] = len(fact_source_keys(node))
                    await vector_store.update_payload(node_id, {"custom": custom})
                    updated += 1
                except Exception as exc:
                    logger.warning(
                        "[S2] mark holistic therapy reviewed failed node_id=%s: %s",
                        node_id, exc,
                    )
        return updated

    async def _run_cross_domain_sweeper(
        self,
        user_id: str,
        agent_id: str,
        llm_call,
        request_id: str = "",
    ) -> Dict[str, Any]:
        """在 System2 Agent 之后运行跨域突破性归纳"""
        if not _SWEEPER_ENABLED:
            return {"skipped": True, "reason": "SWEEPER_ENABLED=false"}

        try:
            from .cross_domain_sweeper import CrossDomainSweeper

            embed_service = self._writer.embed_service
            sweeper = CrossDomainSweeper(
                graph_store=self._graph_store,
                embed_service=embed_service,
                llm_call=llm_call,
                user_id=user_id,
                agent_id=agent_id,
                request_id=request_id,
                cache=self._cache,
            )
            result = await sweeper.sweep()
            logger.info(f"[S2] Cross-domain sweeper: {result}")
            return result
        except Exception as e:
            logger.warning(f"[S2] Cross-domain sweeper failed: {e}", exc_info=True)
            return {"error": str(e)}

    def _get_llm_call(self):
        """返回 sweeper 需要的 async LLM callable: async (prompt: str) -> str"""
        from ..agent.llm_provider import LLMProvider

        provider = LLMProvider(self.config)
        # 不传 temperature：由 provider 统一按 config.llm 解析
        # （自己兜一个 0.3 会绕过用户配置，也会绕过 omit_temperature）
        async def _call(prompt: str) -> str:
            resp = await provider._call_llm(prompt, max_tokens=1024)
            return resp.content

        return _call

    async def _store_system2_log(
        self,
        request_id: str,
        user_id: str,
        agent_id: str,
        step: str,
        result: Dict[str, Any],
    ) -> None:
        """Write a pipeline log entry for a System 2 worker execution."""
        if not self._cache or not request_id:
            return
        try:
            import json as _json
            await self._cache.store_pipeline_log(
                request_id=request_id,
                user_id=user_id,
                agent_id=agent_id,
                step=step,
                prompt=result.get("prompt", ""),
                response=result.get("llm_response", ""),
                parsed=_json.dumps(result, ensure_ascii=False, default=str),
                elapsed_ms=result.get("elapsed_ms", 0),
                prompt_tokens=result.get("prompt_tokens", 0),
                completion_tokens=result.get("completion_tokens", 0),
                total_tokens=result.get("total_tokens", 0),
            )
        except Exception as e:
            logger.debug(f"[S2] store {step} pipeline log failed: {e}")

    # ================================================================
    # 公开 API: evolve (手动触发 System 2)
    # ================================================================

    async def run_sweeper(
        self,
        user_id: str,
        agent_id: str = "default_agent",
    ) -> Dict[str, Any]:
        """
        手动触发 cross-domain sweeper（同步等待完成）。

        独立于 evolve 流程，不执行 S2 Agent，只做：
        1. 补齐 schema 的 beh abstraction + beh_embedding
        2. 碰撞扫描 + core 归纳

        Args:
            user_id: SDK user_id（可含通用 namespace 前缀）
            agent_id: Agent ID

        Returns:
            {"request_id": "...", "basics_count": N, "new_beh_embeddings": N,
             "collisions": N, "cores_created": N, "cores_merged": N,
             "created_memory_ids": [...]}
        """
        import uuid
        request_id = str(uuid.uuid4())
        logger.info(f"[S2] manual sweeper triggered: user={user_id} agent={agent_id} request_id={request_id}")

        result = await self._run_cross_domain_sweeper(
            user_id=user_id,
            agent_id=agent_id,
            llm_call=self._get_llm_call(),
            request_id=request_id,
        )
        result["request_id"] = request_id
        return result

    async def evolve(
        self,
        user_id: str,
        agent_id: str = "default_agent",
        max_batches: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        手动触发 System 2 认知加工（由调用方/App 层控制触发时机）。

        语义：evolve 一次 = 对该 user 的 VDB 全量跑一次 System 2（S2 Agent +
        cross-domain sweeper）。S2 的输入是 prepare_materials 全量扫描的 VDB
        （L2/L4 聚类 + 已有 Graph schema），与写入次数无关，只跑一次完整认知周期。

        非幂等：每次 evolve 看到的是当前最新的 VDB 全量，连续多次 evolve 有意义
        （例如先 add 一批 → evolve → 再 add 一批 → 再 evolve）。

        Args:
            user_id: 用户 ID
            agent_id: Agent ID

        Returns:
            {"success": True, "request_id": "...", "tasks_processed": 1, "results": {...}}
        """
        import uuid
        request_id = str(uuid.uuid4())
        logger.info(f"[S2] evolve triggered: user={user_id} agent={agent_id} request_id={request_id}")

        payload = {
            "user_id": user_id,
            "agent_id": agent_id,
            "session_id": "default_session",
            "memory_id": "",
            "request_id": request_id,
            "agent_stored_ids": [],
            "content": "",
            "created_at": datetime.now().isoformat(),
            "max_batches": max_batches,
        }
        results = await self._run_system2_workers(payload)

        # Sweeper consumes the completed Schema set. Running it after a partial
        # cycle makes its output depend on where the batch budget happened to end.
        # Defer it until this user's current evoling cycle is complete.
        should_continue = bool(results.get("should_continue"))
        if should_continue:
            sweeper_result = {
                "skipped": True,
                "reason": "evoling_cycle_partial",
            }
        else:
            sweeper_result = await self._run_cross_domain_sweeper(
                user_id=user_id,
                agent_id=agent_id,
                llm_call=self._get_llm_call(),
                request_id=request_id,
            )

        agent_success = bool((results.get("system2_agent") or {}).get("success", True))
        first_failure = next(iter(
            (results.get("system2_agent") or {}).get("batch_failures") or []
        ), {})
        return {
            "success": agent_success,
            "request_id": request_id,
            "tasks_processed": 1,
            "results": results,
            "cross_domain_sweeper": sweeper_result,
            "should_continue": should_continue,
            "continuation_reason": results.get("continuation_reason", "complete"),
            "remaining_clusters": int(results.get("remaining_clusters") or 0),
            "remaining_actionable_clusters": int(
                results.get("remaining_actionable_clusters") or 0
            ),
            "remaining_fresh_facts": int(results.get("remaining_fresh_facts") or 0),
            "cycle_status": (
                (results.get("system2_agent") or {}).get("cycle_status") or "complete"
            ),
            "batch_failures": (
                (results.get("system2_agent") or {}).get("batch_failures") or []
            ),
            **({
                "error_code": int(first_failure.get("error_code") or 502),
                "error_message": str(
                    first_failure.get("error") or "System2 evoling batch failed"
                ),
                "failure_kind": first_failure.get("failure_kind") or "batch_failed",
            } if not agent_success else {}),
        }

    # ================================================================
    # Lifecycle
    # ================================================================

    async def close(self) -> None:
        """释放 Pro 写入资源"""
        if self._writer:
            await self._writer.close()

        logger.debug("System2Writer closed")
