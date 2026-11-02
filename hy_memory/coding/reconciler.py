# -*- coding: utf-8 -*-
"""
Coding Memory Reconciler

输入：List[CodingMemoryDraft]（来自 extractor）
输出：List[ReconcileOp] —— ADD / UPDATE / DELETE / SKIP，**ops 数可超过 drafts 数**
        （DELETE 由 LLM 在 draft 与 candidate 关系中观察到"明确否定/作废"时附带产出）

执行端：reconcile() 调用 store 完成实际 ADD / UPDATE / DELETE 持久化操作。
CodingWriter 分两步用：先 plan()（检索 + LLM 决策，不落库），等经验层与事实层的
模型决策都成功后再 apply()（落库）。LLM 失败 / 空回复 / 解析不出时 plan() 抛
CodingWriteError(RECONCILE_FAILED)，本次写入什么都不存。

复用 LLMConfig.model（与 chat reconciler 共用）。

对外 scene 与调用方式见 docs/scene_coding_usage.md。
"""

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..agent.llm_provider import LLMProvider
from ..core.embed_service import EmbedService
from ..prompts import (
    get_prompt,
    normalize_language,
    resolve_language,
    with_output_language,
)
from .judge import _store_coding_log
from .store import CodingMemoryStore
from .types import CodingMemory, CodingMemoryDraft, CodingWriteError, ReconcileOp



logger = logging.getLogger(__name__)


def _resolve_configured_temperature(llm_provider) -> Optional[float]:
    """从 MemoryConfig.llm 解析温度；None = 请求里不带该参数。"""
    config = getattr(llm_provider, "config", None)
    llm = getattr(config, "llm", None) if config is not None else None
    if llm is not None:
        if getattr(llm, "omit_temperature", False):
            return None
        temp = getattr(llm, "temperature", None)
        if temp is not None:
            return float(temp)
    inner = getattr(llm_provider, "_llm_config", None)
    temp = getattr(inner, "temperature", None) if inner is not None else None
    return float(temp) if temp is not None else None


# ================================================================
# Prompt
# ================================================================

from ..prompts.en import CODING_RECONCILE_PROMPT as RECONCILE_PROMPT  # noqa: E402,F401  (re-export)


def _drafts_language_text(drafts: List[CodingMemoryDraft]) -> str:
    """Build language-routing text from new drafts, not retrieved candidates."""
    parts = []
    for draft in drafts:
        parts.extend([
            draft.task,
            *(draft.search_keys or []),
            draft.solution,
            draft.boundary_envs,
        ])
    return "\n".join(str(part).strip() for part in parts if str(part).strip())


def build_experience_reconcile_prompt(
    drafts: List[CodingMemoryDraft],
    candidates_per_draft: List[List[CodingMemory]],
    language: Optional[str] = None,
) -> Tuple[str, str]:
    """Return the experience reconcile prompt and its language code.

    Explicit ``language`` wins; otherwise zh/en auto-detection over the drafts.
    """
    drafts_block = CodingMemoryReconciler._format_drafts(drafts)
    candidates_block = CodingMemoryReconciler._format_candidates(
        drafts, candidates_per_draft,
    )
    explicit = normalize_language(language)
    code = resolve_language(explicit, _drafts_language_text(drafts))
    prompt = get_prompt("CODING_RECONCILE_PROMPT", code).format(
        drafts_block=drafts_block,
        candidates_block=candidates_block,
    )
    return with_output_language(prompt, code, explicit=explicit is not None), code


@dataclass
class ExperienceReconcilePlan:
    """plan() 的产物：模型已决策、尚未落库。apply() 才执行 store 操作。"""

    drafts: List[CodingMemoryDraft]
    candidates_per_draft: List[List[CodingMemory]] = field(default_factory=list)
    ops: List[ReconcileOp] = field(default_factory=list)


# ================================================================
# CodingMemoryReconciler
# ================================================================

class CodingMemoryReconciler:
    """
    Reconcile + 持久化执行器。

    一次调用：
      1. 为每个 draft 用 task embedding 检索 top-3 candidates
      2. 单次 LLM 决策产出 ReconcileOp[]
      3. 调 store 执行 ADD / UPDATE / DELETE
    """

    def __init__(
        self,
        store: CodingMemoryStore,
        embed_service: EmbedService,
        llm_provider: LLMProvider,
        *,
        max_tokens: int = 1500,
        temperature: Optional[float] = None,
        candidates_per_draft: int = 3,
        cache: Any = None,
    ):
        self.store = store
        self.embed_service = embed_service
        self.llm_provider = llm_provider
        self.max_tokens = max_tokens
        # cache 非空时落 CODING_RECONCILE pipeline log（best-effort）
        self.cache = cache
        # None → 复用 LLMConfig.temperature（避免硬编码触发 kimi/deepseek 的温度硬约束）
        if temperature is None:
            # None 一路传到 provider = 不带该参数；此前兜 0.1 会绕过用户配置，
            # 在只接受特定 temperature 的网关上（Opus 5 只收 1 或不传）直接 400
            temperature = _resolve_configured_temperature(llm_provider)
        self.temperature = temperature
        self.candidates_per_draft = candidates_per_draft

    async def reconcile(
        self,
        drafts: List[CodingMemoryDraft],
        *,
        request_id: str = "",
        user_id: str = "",
        agent_id: str = "default_agent",
        session_id: str = "",
        language: Optional[str] = None,
    ) -> List[ReconcileOp]:
        """
        执行 reconcile + 持久化（plan() 后紧接 apply()）。返回执行的 ReconcileOp[]
        （含成功执行的 ADD/UPDATE/DELETE/SKIP）。

        LLM 失败、空回复或解析不出时抛 CodingWriteError(RECONCILE_FAILED)，什么都
        不落库——不再回退成「全部 ADD」。
        """
        plan = await self.plan(
            drafts,
            request_id=request_id,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            language=language,
        )
        return await self.apply(plan)

    async def plan(
        self,
        drafts: List[CodingMemoryDraft],
        *,
        request_id: str = "",
        user_id: str = "",
        agent_id: str = "default_agent",
        session_id: str = "",
        language: Optional[str] = None,
    ) -> ExperienceReconcilePlan:
        """检索 candidates + LLM 决策，不落库。

        有效回复 = 一个 JSON 对象，其 "ops" 是列表：
          - {"ops": []}：模型判定「无需任何操作」（与 chat reconcile 的 [] 同义），
            成功、本层什么都不存；
          - 非空列表：至少要有一条可识别的 op（ADD/UPDATE/DELETE/SKIP），否则视为
            解析失败。漏掉的 draft 仍按原规则补 ADD（那是对有效回复的校验兜底，
            不是模型失败兜底）。
        LLM 调用失败（LLM_UNAVAILABLE）、200 但 content 为空（EMPTY_RESPONSE）、
        回复不是上述形状（JSON_PARSE_FAILED）时抛 CodingWriteError(RECONCILE_FAILED)。

        cache 非空时落 CODING_RECONCILE pipeline log（成功、失败都落）。
        """
        if not drafts:
            return ExperienceReconcilePlan(drafts=[])

        # 从 draft 回填 trace 身份（writer 通常显式传，这里兜底）
        user_id = user_id or drafts[0].user_id
        agent_id = agent_id or drafts[0].agent_id
        session_id = session_id or (drafts[0].session_id or "")

        # 1. 检索 candidates
        candidates_per_draft: List[List[CodingMemory]] = await self._retrieve_candidates(drafts)

        # 2. LLM 决策
        _t0 = time.perf_counter()
        _decide_prompt = ""
        _decide_raw = ""
        _decide_resp = None
        failure: Optional[Tuple[str, str]] = None
        ops: List[ReconcileOp] = []
        try:
            ops, _decide_prompt, _decide_raw, _decide_resp = await self._llm_decide(
                drafts, candidates_per_draft, language=language,
            )
            failure = self._reply_failure(_decide_raw)
        except Exception as e:
            logger.warning(f"[coding-reconcile] LLM decide failed: {e}")
            _decide_raw = f"[LLM error] {type(e).__name__}: {str(e)[:300]}"
            failure = (
                "LLM_UNAVAILABLE",
                f"coding reconcile LLM call failed: {type(e).__name__}: {e}",
            )

        _decide_elapsed = round((time.perf_counter() - _t0) * 1000, 2)
        # CODING_RECONCILE：LLM 决策完成即打点（执行入库之前，crash 不丢）
        await _store_coding_log(
            self.cache, request_id=request_id, user_id=user_id, agent_id=agent_id,
            session_id=session_id, step="CODING_RECONCILE",
            prompt=_decide_prompt, response=_decide_raw,
            parsed=json.dumps(
                {"error": failure[0], "message": failure[1], "stored": 0}
                if failure else {"ops": [o.to_dict() for o in ops]},
                ensure_ascii=False, default=str,
            ),
            elapsed_ms=_decide_elapsed,
            prompt_tokens=getattr(_decide_resp, "prompt_tokens", 0) if _decide_resp else 0,
            completion_tokens=getattr(_decide_resp, "completion_tokens", 0) if _decide_resp else 0,
            total_tokens=getattr(_decide_resp, "tokens_used", 0) if _decide_resp else 0,
        )
        if failure is not None:
            code, message = failure
            logger.warning(f"[coding-reconcile] {code}: {message}")
            raise CodingWriteError("RECONCILE_FAILED", message, cause=code)

        return ExperienceReconcilePlan(
            drafts=list(drafts),
            candidates_per_draft=candidates_per_draft,
            ops=ops,
        )

    async def apply(self, plan: ExperienceReconcilePlan) -> List[ReconcileOp]:
        """执行 plan() 的决策（store ADD / UPDATE / DELETE）。"""
        if not plan.drafts:
            return []
        if not plan.ops:
            # 有效的 {"ops": []}：模型判定无需任何操作，本层什么都不存。
            logger.info(
                f"[coding-reconcile] drafts={len(plan.drafts)} LLM decided no ops"
            )
            return []
        executed = await self._execute(
            plan.ops, plan.drafts, plan.candidates_per_draft,
        )
        logger.info(
            f"[coding-reconcile] drafts={len(plan.drafts)} ops={len(executed)} "
            + ", ".join(f"{a}={sum(1 for o in executed if o.action == a)}"
                        for a in ("ADD", "UPDATE", "DELETE", "SKIP"))
        )
        return executed

    # ------------------------------------------------------------
    # 检索 candidates
    # ------------------------------------------------------------

    async def _retrieve_candidates(
        self, drafts: List[CodingMemoryDraft]
    ) -> List[List[CodingMemory]]:
        """每个 draft 用 task + search_keys 多路 embedding 检索同 user 的 top-N candidates。

        只用 task 检索时，若新 draft 的 task 措辞与旧记忆差异较大（同一件事换个
        说法），会漏召回本应合并的候选。这里把 search_keys 一并作为检索入口，
        多路召回后按 experience_id 去重（保留最高分），扩大候选覆盖面，降低漏判 ADD。
        """
        result: List[List[CodingMemory]] = []
        for d in drafts:
            # A coding mutation must be attributable to one conversation.
            # Empty/legacy session metadata stays readable through ordinary
            # search, but it cannot authorize UPDATE/DELETE.
            if not d.session_id:
                result.append([])
                continue
            try:
                # task 为主入口，search_keys 为附加入口（去重、去空）
                queries = [d.task] + [k for k in (d.search_keys or []) if k and k != d.task]
                embs = await self.embed_service.embed_batch(queries)

                # 多路检索 → 按 experience_id 聚合，保留最高分
                best: Dict[str, float] = {}
                for emb in embs:
                    hits = await self.store.search_by_query_embedding(
                        emb,
                        user_id=d.user_id,
                        agent_id=d.agent_id,
                        session_id=d.session_id,
                        workspace_id=d.workspace_id,
                        branch=d.branch,
                        top=self.candidates_per_draft * 4,
                        include_sources=[d.source],
                    )
                    for h in hits:
                        eid = h["experience_id"]
                        sc = h.get("score", 0.0)
                        if eid not in best or sc > best[eid]:
                            best[eid] = sc
                # 按分数排序取 top-N
                cand_ids = [eid for eid, _ in sorted(best.items(), key=lambda x: -x[1])][
                    : self.candidates_per_draft
                ]
                cands = await self.store.get_many(cand_ids, user_id=d.user_id)
                result.append(cands)
            except Exception as e:
                logger.warning(f"[coding-reconcile] retrieve candidates failed: {e}")
                result.append([])
        return result

    # ------------------------------------------------------------
    # LLM 决策
    # ------------------------------------------------------------

    async def _llm_decide(
        self,
        drafts: List[CodingMemoryDraft],
        candidates_per_draft: List[List[CodingMemory]],
        language: Optional[str] = None,
    ) -> Tuple[List[ReconcileOp], str, str, Any]:
        """返回 (ops, prompt, raw_response, llm_resp)，供上层打 CODING_RECONCILE log。"""
        prompt, _ = build_experience_reconcile_prompt(
            drafts, candidates_per_draft, language=language,
        )

        resp = await self.llm_provider.complete(
            prompt=prompt,
            max_tokens=self.max_tokens,
            audit_stage="coding_reconcile",
        )
        return self._parse_ops(resp.content), prompt, resp.content, resp

    @staticmethod
    def _format_drafts(drafts: List[CodingMemoryDraft]) -> str:
        rows = []
        for i, d in enumerate(drafts):
            rows.append(
                f"draft[{i}]:\n"
                f"  task: {d.task!r}\n"
                f"  search_keys: {d.search_keys}\n"
                f"  solution: {_truncate(d.solution, 800)!r}\n"
                f"  boundary_envs: {_truncate(d.boundary_envs, 400)!r}"
            )
        return "\n\n".join(rows) if rows else "(none)"

    @staticmethod
    def _format_candidates(
        drafts: List[CodingMemoryDraft],
        candidates_per_draft: List[List[CodingMemory]],
    ) -> str:
        rows = []
        for i, d in enumerate(drafts):
            cands = candidates_per_draft[i] if i < len(candidates_per_draft) else []
            if not cands:
                rows.append(f"for draft[{i}]: (no candidates)")
                continue
            cand_rows = []
            for c in cands:
                cand_rows.append(
                    f"  - experience_id={c.experience_id}\n"
                    f"    task: {c.task!r}\n"
                    f"    solution: {_truncate(c.solution, 600)!r}\n"
                    f"    boundary_envs: {_truncate(c.boundary_envs, 300)!r}\n"
                    f"    updated_at: {c.updated_at.isoformat() if c.updated_at else 'n/a'}"
                )
            rows.append(f"for draft[{i}]:\n" + "\n".join(cand_rows))
        return "\n\n".join(rows) if rows else "(none)"

    @classmethod
    def _reply_failure(cls, text: Optional[str]) -> Optional[Tuple[str, str]]:
        """回复本身是失败时返回 (cause, message)；有效回复（含 {"ops": []}）返回 None。"""
        if not (text or "").strip():
            return "EMPTY_RESPONSE", "coding reconcile LLM returned empty response"
        obj = cls._load_object(text)
        ops_raw = obj.get("ops") if isinstance(obj, dict) else None
        if not isinstance(ops_raw, list):
            return (
                "JSON_PARSE_FAILED",
                f"coding reconcile output has no ops list ({len(text)} chars)",
            )
        if ops_raw and not cls._parse_ops(text):
            return (
                "JSON_PARSE_FAILED",
                f"coding reconcile output has no recognizable op ({len(text)} chars)",
            )
        return None

    @staticmethod
    def _load_object(text: Optional[str]) -> Optional[Dict[str, Any]]:
        """宽松解析出顶层 JSON 对象；解析不出返回 None。"""
        if not text:
            return None
        s = text.strip()
        # 去 markdown fence
        if s.startswith("```"):
            lines = s.split("\n")
            body, in_fence = [], False
            for line in lines:
                if line.startswith("```"):
                    in_fence = not in_fence
                    continue
                body.append(line)
            s = "\n".join(body).strip() or s

        obj: Optional[Dict[str, Any]] = None
        try:
            obj = json.loads(s)
        except Exception:
            start = s.find("{")
            end = s.rfind("}")
            if start != -1 and end > start:
                try:
                    obj = json.loads(s[start: end + 1])
                except Exception:
                    obj = None
        return obj if isinstance(obj, dict) else None

    @classmethod
    def _parse_ops(cls, text: str) -> List[ReconcileOp]:
        """宽松解析 ops 列表。"""
        if not text:
            return []
        obj = cls._load_object(text)
        if not obj:
            logger.warning(
                f"[coding-reconcile] failed to parse ops output ({len(text or '')} chars)"
            )
            logger.debug(f"[coding-reconcile] unparsable ops output: {text!r}")
            return []
        ops_raw = obj.get("ops") or []
        if not isinstance(ops_raw, list):
            return []

        ops: List[ReconcileOp] = []
        for raw in ops_raw:
            if not isinstance(raw, dict):
                continue
            action = (raw.get("action") or "").strip().upper()
            if action not in ("ADD", "UPDATE", "DELETE", "SKIP"):
                continue
            di = raw.get("draft_idx")
            if di is not None:
                try:
                    di = int(di)
                except Exception:
                    di = None
            ops.append(ReconcileOp(
                action=action,  # type: ignore[arg-type]
                draft_idx=di,
                target_experience_id=raw.get("target_experience_id"),
                reason=raw.get("reason"),
            ))
        return ops

    # ------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------

    async def _execute(
        self,
        ops: List[ReconcileOp],
        drafts: List[CodingMemoryDraft],
        candidates_per_draft: List[List[CodingMemory]],
    ) -> List[ReconcileOp]:
        """
        校验 + 执行 ops。返回成功执行的 ops（filter 掉无效 op）。

        校验规则：
        - ADD     需 draft_idx 合法
        - UPDATE  需 draft_idx 合法 + target_experience_id 非空
        - DELETE  需 target_experience_id 非空
        - SKIP    不执行任何 store 操作

        向量化失败（CodingWriteError(EMBEDDING_FAILED)）不吞成「少执行一条」，
        原样抛给 CodingWriter，整次写入报失败。
        """
        executed: List[ReconcileOp] = []
        # 已被 UPDATE 占用的 draft_idx → 防止同 draft 被重复 ADD/UPDATE
        consumed: set = set()

        for op in ops:
            if op.action == "ADD":
                if op.draft_idx is None or not (0 <= op.draft_idx < len(drafts)):
                    logger.warning(f"[coding-reconcile] drop invalid ADD: {_op_ref(op)}")
                    continue
                if op.draft_idx in consumed:
                    logger.warning(f"[coding-reconcile] drop ADD on consumed draft: {_op_ref(op)}")
                    continue
                d = drafts[op.draft_idx]
                memory = self._draft_to_new_memory(d)
                try:
                    await self.store.insert(memory)
                    op.target_experience_id = memory.experience_id  # 回写 id 供 caller 读
                    executed.append(op)
                    consumed.add(op.draft_idx)
                except CodingWriteError:
                    raise
                except Exception as e:
                    logger.warning(f"[coding-reconcile] ADD failed: {e}")

            elif op.action == "UPDATE":
                if op.draft_idx is None or not (0 <= op.draft_idx < len(drafts)):
                    logger.warning(f"[coding-reconcile] drop invalid UPDATE: {_op_ref(op)}")
                    continue
                if not op.target_experience_id:
                    logger.warning(f"[coding-reconcile] drop UPDATE missing target: {_op_ref(op)}")
                    continue
                if not self._target_was_offered(op, candidates_per_draft):
                    logger.warning(
                        f"[coding-reconcile] drop UPDATE outside authority scope: {_op_ref(op)}"
                    )
                    continue
                if op.draft_idx in consumed:
                    logger.warning(f"[coding-reconcile] drop UPDATE on consumed draft: {_op_ref(op)}")
                    continue
                d = drafts[op.draft_idx]
                memory = self._draft_to_new_memory(d, experience_id=op.target_experience_id)
                # UPDATE 覆盖正文不留痕，但事实链是元数据，得保下来（§4：UPDATE 取并集）
                prev = await self.store.get_by_id(
                    op.target_experience_id, user_id=d.user_id
                )
                if prev is not None:
                    memory.source_fact_ids = list(prev.source_fact_ids)
                try:
                    await self.store.update(memory)
                    executed.append(op)
                    consumed.add(op.draft_idx)
                except CodingWriteError:
                    raise
                except Exception as e:
                    logger.warning(f"[coding-reconcile] UPDATE failed: {e}")

            elif op.action == "DELETE":
                if not op.target_experience_id:
                    logger.warning(f"[coding-reconcile] drop DELETE missing target: {_op_ref(op)}")
                    continue
                if not self._target_was_offered(op, candidates_per_draft):
                    logger.warning(
                        f"[coding-reconcile] drop DELETE outside authority scope: {_op_ref(op)}"
                    )
                    continue
                # DELETE 需要知道 user_id；通过 draft_idx（如有）或第一个 draft 推断
                user_id = self._infer_user_id(op, drafts)
                if not user_id:
                    logger.warning(f"[coding-reconcile] drop DELETE no user_id context: {_op_ref(op)}")
                    continue
                try:
                    ok = await self.store.delete(op.target_experience_id, user_id=user_id)
                    if ok:
                        executed.append(op)
                except Exception as e:
                    logger.warning(f"[coding-reconcile] DELETE failed: {e}")

            elif op.action == "SKIP":
                if op.draft_idx is not None and 0 <= op.draft_idx < len(drafts):
                    consumed.add(op.draft_idx)
                executed.append(op)

        # 兜底：每个 draft 至少要被处理一次
        for i in range(len(drafts)):
            if i in consumed:
                continue
            logger.info(f"[coding-reconcile] draft[{i}] not handled, fallback ADD")
            d = drafts[i]
            memory = self._draft_to_new_memory(d)
            try:
                await self.store.insert(memory)
                executed.append(ReconcileOp(
                    action="ADD", draft_idx=i,
                    target_experience_id=memory.experience_id,
                    reason="fallback ADD (no LLM op)",
                ))
            except CodingWriteError:
                raise
            except Exception as e:
                logger.warning(f"[coding-reconcile] fallback ADD failed: {e}")

        return executed

    @staticmethod
    def _target_was_offered(
        op: ReconcileOp,
        candidates_per_draft: List[List[CodingMemory]],
    ) -> bool:
        """Only mutate a target frozen into this draft's scoped candidate set."""
        if op.draft_idx is None or not (0 <= op.draft_idx < len(candidates_per_draft)):
            return False
        return any(
            candidate.experience_id == op.target_experience_id
            for candidate in candidates_per_draft[op.draft_idx]
        )

    @staticmethod
    def _infer_user_id(
        op: ReconcileOp, drafts: List[CodingMemoryDraft]
    ) -> Optional[str]:
        if op.draft_idx is not None and 0 <= op.draft_idx < len(drafts):
            return drafts[op.draft_idx].user_id
        # 后备：用 drafts 里第一个非空 user_id（同一 reconcile 调用通常同 user）
        for d in drafts:
            if d.user_id:
                return d.user_id
        return None

    @staticmethod
    def _draft_to_new_memory(
        d: CodingMemoryDraft,
        *,
        experience_id: Optional[str] = None,
    ) -> CodingMemory:
        return CodingMemory(
            experience_id=experience_id or str(uuid.uuid4()),
            user_id=d.user_id,
            agent_id=d.agent_id,
            task=d.task,
            search_keys=list(d.search_keys),
            solution=d.solution,
            boundary_envs=d.boundary_envs,
            workspace_id=d.workspace_id,
            branch=d.branch,
            session_id=d.session_id,
            files=list(d.files),
            confidence=d.confidence,
            source=d.source,
            extraction_source=d.extraction_source,
            memory_at=d.memory_at,
        )


# ================================================================
# Helpers
# ================================================================

def _op_ref(op: ReconcileOp) -> str:
    """op 的日志引用：只带动作与编号，不带 LLM 写的 reason。"""
    return (
        f"action={op.action} draft_idx={op.draft_idx} "
        f"target={op.target_experience_id}"
    )


def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    if len(s) <= n:
        return s
    return s[: n - 3] + "..."
