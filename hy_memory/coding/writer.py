# -*- coding: utf-8 -*-
"""
CodingWriter - 写入端协调器

把 judge / preproc / extractor / 两个 reconciler / store 串成一条端到端流程：

    classify_messages_scene(messages, llm)
        ├── chat → caller 把 messages strip_tool_messages 后走 chat 链
        └── working / coding
             ├── recent history: 最近 K 个 user-turn（只作上下文）
             ├── new messages: 本次全部 turn（只做单条限长）
             ├── extractor.extract(...)            ← 一次 LLM 产出双层草稿
             ├── reconciler.plan(经验草稿)          ← ADD/UPDATE/DELETE/SKIP 决策
             ├── fact_reconciler.plan(事实草稿)      ← INSERT/SUPERSEDE 决策
             ├── reconciler.apply / fact_reconciler.apply ← 两层决策都成功后才落库
             └── _crosslink(...)                   ← 同批全互链回填
"""

import logging
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..agent.llm_provider import LLMProvider
from ..core.embed_service import EmbedService
from ..pipelines.base import ChatMessage
from .extractor import CodingMemoryExtractor
from .fact_reconciler import WorkingFactReconciler
from .judge import classify_messages_is_coding
from .preproc import has_any_tool_message, prepare_extractor_messages
from .reconciler import CodingMemoryReconciler
from .store import CodingMemoryStore
from .types import CodingWriteError, FactReconcileOp, ReconcileOp, WorkingFact

logger = logging.getLogger(__name__)


class CodingWriter:
    """
    Coding 写入端协调器。

    生命周期：依赖外部传入的 store / extractor / reconciler；不负责 initialize/close。
    """

    def __init__(
        self,
        store: CodingMemoryStore,
        extractor: CodingMemoryExtractor,
        reconciler: CodingMemoryReconciler,
        llm_provider: LLMProvider,
        embed_service: EmbedService,
        fact_reconciler: Optional[WorkingFactReconciler] = None,
        cache: Any = None,
        variant: str = "working",
    ):
        self.store = store
        self.extractor = extractor
        self.reconciler = reconciler
        self.llm_provider = llm_provider
        self.embed_service = embed_service
        self.fact_reconciler = fact_reconciler or WorkingFactReconciler(
            store, embed_service, llm_provider
        )
        self.variant = (
            "coding"
            if str(variant or "").strip().lower() == "coding"
            else "working"
        )
        # cache 仅用于把 request_id 串给各阶段（各阶段自己打点）；writer 不集中打点。
        self.cache = cache

    async def write(
        self,
        messages: List[ChatMessage],
        *,
        user_id: str,
        agent_id: str = "default_agent",
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: Optional[str] = None,
        source: str = "default",
        memory_at: Optional[datetime] = None,
        language: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        working/coding 段写入端到端。memory_at 是对话时间，记到这次写出的经验和
        事实上；不传则用收到请求的时间（与 chat 链路一致）。language 是显式 prompt
        语言码，透传给抽取、经验 reconcile、事实 reconcile 与演变总结；None 时各步
        沿用 zh/en 自动检测。返回响应 dict（兼容 client.add 返回结构）：
            {
                "success": True,
                "scene": "productivity",
                "request_id": "...",
                "elapsed_ms": ...,
                "ops": [
                    {"action": "ADD", "draft_idx": 0, "target_experience_id": "...", "reason": ...},
                    ...
                ],
                "experience_ids": ["..."],   # 所有 ADD/UPDATE 涉及的 experience_id
                "fact_ops": [
                    {"action": "INSERT", "draft_idx": 0, "fact_id": "...", ...},
                    ...
                ],
                "fact_ids": ["..."],         # 这趟真正落库的 fact_id
            }

        模型失败（抽取 LLM 调用失败 / 空回复 / 解析不出）或向量化失败时返回
        success=False，agent_error_code 为 LLM_ERROR / EMPTY_RESPONSE /
        JSON_PARSE_FAILED / EMBEDDING_FAILED，error_code=502。经验层 / 事实层
        reconcile 的模型决策失败时 agent_error_code=RECONCILE_FAILED，原因在
        reconcile_error_code（LLM_UNAVAILABLE / EMPTY_RESPONSE / JSON_PARSE_FAILED），
        本次什么都不落库。模型答了但没有值得记的内容仍是 success=True、0 条。
        """
        request_id = request_id or str(uuid.uuid4())
        t0 = time.perf_counter()
        if memory_at is None:
            memory_at = datetime.now()

        if not messages:
            return self._empty_response(request_id, t0)

        tool_max_bytes = int(
            getattr(self.store.config.coding, "tool_result_max_bytes", 2048) or 2048
        )

        # 1. 历史与新输入分离。
        #    - history_messages：之前 write 的最近 5 个 user-turn，仅作上下文
        #    - current_messages：本次 write 的全部 turn，只限制单条消息体积，不裁 turn
        history_turns = int(
            getattr(self.store.config.extractor, "history_turns", 5) or 5
        )
        try:
            history_messages = await self.store.get_recent_trace_messages(
                user_id=user_id,
                agent_id=agent_id or "default_agent",
                max_user_turns=history_turns,
                source=source,
            )
        except Exception as e:
            logger.warning(f"[coding-write] recent trace lookup failed, continue without history: {e}")
            history_messages = []
        current_messages = prepare_extractor_messages(
            messages, tool_max_bytes=tool_max_bytes,
        )

        # 2. 抽取双层 drafts（一次 LLM 调用）
        #
        # 刻意不给 extractor 看用户已有的 task。抽取只该忠实读这一段发生了什么，
        # 跟库里已有的什么关系是 reconcile 的事。把已有 task 摆到它面前，它会把
        # 一个其实不同的任务往清单里某个措辞上靠，reconciler 随后判成 UPDATE 合并
        # 掉，那个区别就没了——措辞对不上顶多多存一条，措辞被带偏是直接写错。
        #
        # 模型失败（LLM 调用失败 / 空回复 / 解析不出）抛 CodingWriteError：整次写入
        # 报失败、什么都不存，连 trace 也不追加——调用方换个 request_id 重跑时，
        # 历史窗口里不会多出这段本该是「新消息」的对话。
        try:
            drafts, fact_drafts = await self.extractor.extract(
                messages,
                history_messages=history_messages,
                tool_result_max_bytes=tool_max_bytes,
                user_id=user_id,
                agent_id=agent_id,
                workspace_id=workspace_id,
                branch=branch,
                session_id=session_id,
                request_id=request_id,
                language=language,
            )
        except CodingWriteError as e:
            return self._failure_response(request_id, t0, e)

        trace_kwargs = dict(
            request_id=request_id,
            user_id=user_id,
            agent_id=agent_id or "default_agent",
            session_id=session_id,
            workspace_id=workspace_id,
            branch=branch,
            source=source,
        )

        if not drafts and not fact_drafts:
            # 模型答了、只是没有值得记的：正常的空抽取，仍算成功。历史服务于下一次
            # write；即使本次没跨过价值门槛，也不能丢掉上下文。
            logger.info(
                f"[coding-write] no drafts produced (value bar); request_id={request_id}"
            )
            await self._append_trace(current_messages, **trace_kwargs)
            return self._empty_response(request_id, t0)

        # extractor 只负责内容；调用来源由 SDK 入口注入，不能交给 LLM 生成。
        for draft in drafts:
            draft.source = source
            draft.memory_at = memory_at

        # 3. 两层 reconcile 的模型决策（只检索 + 调 LLM，不落库）
        # 4. 两层决策都成功后才落库：经验层 apply → 事实层 apply
        #
        # 先把所有模型决策做完再写库，任何一层的决策失败（LLM 失败 / 空回复 /
        # 解析不出 → CodingWriteError(RECONCILE_FAILED)）都发生在落库之前，本次
        # 写入一条经验 / 事实都没存、也没覆盖旧事实，换个 request_id 重跑即干净。
        # 两层决策互不依赖（各自检索各自的候选），提前做不改变结果。
        #
        # 向量化失败抛 CodingWriteError(EMBEDDING_FAILED)：不落没有向量的节点，
        # 整次写入报失败。
        facts: List[WorkingFact] = []
        fact_ops: List[FactReconcileOp] = []
        ops: List[ReconcileOp] = []
        try:
            experience_plan = (
                await self.reconciler.plan(
                    drafts,
                    request_id=request_id,
                    user_id=user_id,
                    agent_id=agent_id,
                    session_id=session_id or "",
                    language=language,
                )
                if drafts else None
            )
            fact_plan = (
                await self.fact_reconciler.plan(
                    fact_drafts,
                    user_id=user_id,
                    agent_id=agent_id,
                    session_id=session_id,
                    workspace_id=workspace_id,
                    branch=branch,
                    source=source,
                    memory_at=memory_at,
                    language=language,
                )
                if fact_drafts else None
            )
            if experience_plan is not None:
                ops = await self.reconciler.apply(experience_plan)
            if fact_plan is not None:
                facts, fact_ops = await self.fact_reconciler.apply(fact_plan)
        except CodingWriteError as e:
            return self._failure_response(request_id, t0, e)

        experience_ids = [op.target_experience_id for op in ops
                          if op.action in ("ADD", "UPDATE") and op.target_experience_id]
        fact_ids = [f.fact_id for f in facts]

        # 5. 同批全互链
        await self._crosslink(experience_ids, facts, user_id=user_id)
        await self._append_trace(current_messages, **trace_kwargs)

        add_cnt = sum(1 for op in ops if op.action == "ADD")
        update_cnt = sum(1 for op in ops if op.action == "UPDATE")
        delete_cnt = sum(1 for op in ops if op.action == "DELETE")
        skip_cnt = sum(1 for op in ops if op.action == "SKIP")
        ops_summary = {
            "add": add_cnt,
            "update": update_cnt,
            "delete": delete_cnt,
            "skip": skip_cnt,
            "total": add_cnt + update_cnt + delete_cnt + skip_cnt,
        }
        insert_cnt = sum(1 for op in fact_ops if op.action == "INSERT")
        supersede_cnt = sum(1 for op in fact_ops if op.action == "SUPERSEDE")

        return {
            "success": True,
            "scene": "productivity",
            "variant": self.variant,
            "request_id": request_id,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            "ops": [op.to_dict() for op in ops],
            "ops_summary": ops_summary,
            "experience_ids": experience_ids,
            "fact_ops": [op.to_dict() for op in fact_ops],
            "fact_ops_summary": {
                "insert": insert_cnt,
                "supersede": supersede_cnt,
                "total": insert_cnt + supersede_cnt,
            },
            "fact_ids": fact_ids,
        }

    # ------------------------------------------------------------
    # 互链
    # ------------------------------------------------------------

    async def _crosslink(
        self,
        experience_ids: List[str],
        facts: List[WorkingFact],
        *,
        user_id: str,
    ) -> None:
        """把同一批落库的经验与事实两两连起来。

        只连真入库的：经验取 ADD/UPDATE 的 id，事实取真正 INSERT 的；SKIP 和
        DELETE 天然不在名单里。两边都取并集——UPDATE 的旧链要保留，事实那边可能
        已经从 SUPERSEDE 过继来一批经验，不能覆盖掉。
        """
        if not experience_ids and not facts:
            return
        fact_ids = [f.fact_id for f in facts]

        # 正向：经验 ← 事实
        if fact_ids:
            for eid in experience_ids:
                try:
                    exp = await self.store.get_by_id(eid, user_id=user_id)
                    if exp is None:
                        continue
                    merged = _union(exp.source_fact_ids, fact_ids)
                    if merged != list(exp.source_fact_ids):
                        await self.store.set_experience_source_facts(eid, merged)
                except Exception as e:
                    logger.warning(f"[coding-write] crosslink experience {eid} failed: {e}")

        # 反向：事实 ← 经验
        if experience_ids:
            for f in facts:
                try:
                    merged = _union(f.derived_experience_ids, experience_ids)
                    if merged != list(f.derived_experience_ids):
                        await self.store.set_fact_derived_experiences(f.fact_id, merged)
                        f.derived_experience_ids = merged
                except Exception as e:
                    logger.warning(
                        f"[coding-write] crosslink fact {f.fact_id} failed: {e}"
                    )

    async def _append_trace(self, current_messages: List[ChatMessage], **kwargs) -> None:
        """把本次对话追加进 recent trace（下一次 write 的历史上下文），best-effort。"""
        try:
            await self.store.append_trace(current_messages, **kwargs)
        except Exception as e:
            logger.warning(f"[coding-write] append trace failed: {e}")

    def _failure_response(
        self, request_id: str, t0: float, error: CodingWriteError,
    ) -> Dict[str, Any]:
        """模型 / 向量化失败：success=False，稳定失败码在 agent_error_code。

        形状与 chat 链路的失败回执一致（error_code=502，error_message 带
        「[CODE] 」前缀），调用方据 success 判定并重跑。
        """
        code = error.error_code
        cause = getattr(error, "cause", None)
        message = f"{cause}: {error}" if cause else str(error)
        logger.warning(
            f"[coding-write] failed: code={code} request_id={request_id}: {message}"
        )
        resp = self._empty_response(request_id, t0)
        resp.update({
            "success": False,
            "error_code": 502,
            "error_message": f"[{code}] {message}",
            "agent_status": "failed",
            "agent_error_code": code,
            "agent_error": message,
        })
        if cause:
            # 与 chat 链路一致：reconcile 失败的具体原因
            resp["reconcile_error_code"] = cause
        return resp

    def _empty_response(self, request_id: str, t0: float) -> Dict[str, Any]:
        return {
            "success": True,
            "scene": "productivity",
            "variant": self.variant,
            "request_id": request_id,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 2),
            "ops": [],
            "ops_summary": {"add": 0, "update": 0, "delete": 0, "skip": 0, "total": 0},
            "experience_ids": [],
            "fact_ops": [],
            "fact_ops_summary": {"insert": 0, "supersede": 0, "total": 0},
            "fact_ids": [],
        }


def _union(base: List[str], extra: List[str]) -> List[str]:
    """保序并集。"""
    out = list(base)
    seen = set(out)
    for x in extra:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ================================================================
# Top-level helper（client.async_add 用）
# ================================================================

async def is_coding_segment(
    messages: List[ChatMessage],
    llm_provider: LLMProvider,
) -> bool:
    """
    判定整段 messages 是否 coding 场景。无 tool 消息直接 False（O(1) 短路）。
    """
    if not has_any_tool_message(messages):
        return False
    return await classify_messages_is_coding(messages, llm_provider)
