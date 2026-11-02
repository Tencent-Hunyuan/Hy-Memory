# -*- coding: utf-8 -*-
"""
Working Fact Reconciler（事实层，独立于经验层 reconciler）

输入：List[WorkingFactDraft]（来自 extractor）
输出：(List[WorkingFact], List[FactReconcileOp])

每条 draft 是一段 trace/session 的整体记录，必定 INSERT。唯一的判断是：新 trace
是不是在继续推进旧 trace 的同一个用户任务？是的话把旧事实 SUPERSEDE 掉，形成
跨 session 的任务进展链——软删而不是物理删，fact_link 留着可回溯，同时把旧事实
攒下的经验链**过继**给新事实，免得经验随旧事实一起沉默。

复用 LLMConfig.model（与 chat / 经验层 reconciler 共用）。

"""

import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..agent.llm_provider import LLMProvider
from ..core.embed_service import EmbedService
from ..prompts import (
    get_prompt,
    normalize_language,
    resolve_language,
    with_output_language,
)
from .store import CodingMemoryStore
from .types import (


    MAX_FACT_CHAIN_ENTRIES,
    CodingWriteError,
    FactReconcileOp,
    WorkingFact,
    WorkingFactDraft,
    normalize_subject,
    short_date,
)

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

# 演变总结的固定输出上限，用于容纳多阶段任务的关键动作、直接结果和遗留状态，
# 同时避免复制整条历史。它是摘要契约的一部分，不是需要按实例调节的参数。
_CHAIN_SUMMARY_MAX_TOKENS = 600

# ---- 被覆盖事实的 details 过继 ----
#
# 为什么要有这个：fact_chain_summary 是 LLM 生成的压缩产物，它的提示词第一条就是
# "This is compression only"，且输出被 _CHAIN_SUMMARY_MAX_TOKENS=600 卡住。链越长，
# 越早的代际在摘要里丢得越多，最先丢的是具体取值（价格区间、评分范围这类数值），
# 所以摘要能保住叙事，保不住数值。
#
# 过继走的是复制而不是生成：没有 LLM 调用，没有它的失败模式，保真度在预算内是 100%。
# 且 details 刻意不进向量（store._upsert_fact_vec），所以追加多少都不改变召回打分，
# 只改变"命中之后读得到什么"。这两点合起来是这个做法成立的全部理由。
#
# 三个必须处理的问题：
#   1. 指数膨胀。祖先的 details 里可能已经含着它自己那一代过继来的块，直接追加会
#      让每代翻倍。所以只取每条祖先"自己的" details（按标记切掉它的过继段），血统
#      本来就由 _collect_history 一次走全，不靠逐代累积。
#   2. 上下文预算。如果调用方把 details 原样注入上下文，k 条事实各带多代过继内容
#      会迅速吃穿上下文。所以有总预算。
#   3. 预算内的取舍。按代际由近到远填，填不下就停——近代的具体取值比远代的更可能
#      还有效。这一步是有损的，损在哪里会写进过继块的尾注，不静默截断。
_DETAILS_INHERIT_MARKER = "--- inherited from superseded facts ---"# 总预算。约等于抽取器单条 details 的上限（_MAX_DETAILS_CHARS=4000），即"过继来的
# 全部历史"最多再占一条事实的份量，不让历史盖过当前状态。
_MAX_INHERITED_DETAILS_CHARS = 4000
# 单条祖先的上限，防一条巨型 details 把整个预算吃光、后面几代一个字都进不来。
_MAX_INHERITED_PER_FACT_CHARS = 1200
_INHERIT_DETAILS_DEFAULT = True
ENV_INHERIT_DETAILS = "HY_FACT_INHERIT_DETAILS"


def _inherit_details_default() -> bool:
    """同 `_subject_gate_default`：环境变量兜底，构造参数优先。"""
    raw = os.getenv(ENV_INHERIT_DETAILS)
    if raw is None or not raw.strip():
        return _INHERIT_DETAILS_DEFAULT
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _own_details(details: str) -> str:
    """剥掉一条事实自己过继来的段，只留它本代产生的 details。

    没有标记就原样返回。这是防指数膨胀的关键一步：血统由 _collect_history 一次走全，
    每代只贡献自己那一份。
    """
    if not details:
        return ""
    head = details.split(_DETAILS_INHERIT_MARKER, 1)[0]
    return head.rstrip()


def _build_inherited_details(history: List[WorkingFact]) -> Tuple[str, int, int]:
    """把历代祖先自己的 details 拼成一个带代际标记的过继块。

    Args:
        history: 时间正序的历代事实（_collect_history 的返回值）。

    Returns:
        (过继块文本, 收进去的代数, 因预算被跳过的代数)。没有任何可过继内容时返回 ("", 0, 0)。
    """
    # 由近到远填：近代的具体取值更可能还有效，且摘要对远代的保真度本来就最低，
    # 但预算是硬的，先保住最可能被用到的那部分。
    usable = [(i, f) for i, f in enumerate(history) if _own_details(f.details)]
    blocks: List[Tuple[int, str]] = []
    used = 0
    skipped = 0
    for pos, fact in reversed(usable):
        body = _own_details(fact.details)
        if len(body) > _MAX_INHERITED_PER_FACT_CHARS:
            body = body[:_MAX_INHERITED_PER_FACT_CHARS] + "\n…(truncated)"
        label = f"[gen{pos + 1}"
        if fact.subject:
            label += f" · {fact.subject}"
        # created_at 是 datetime，short_date 吃 ISO 串（同 _summarize_chain 的用法）
        stamp = short_date(fact.created_at.isoformat() if fact.created_at else "")
        if stamp:
            label += f" · {stamp}"
        label += "]"
        chunk = f"{label}\n{body}"
        if used + len(chunk) > _MAX_INHERITED_DETAILS_CHARS:
            skipped += 1
            continue
        used += len(chunk)
        blocks.append((pos, chunk))
    if not blocks:
        return "", 0, skipped
    # 呈现按时间正序，读起来是一条推进线；预算分配才是由近到远。
    blocks.sort(key=lambda item: item[0])
    text = f"{_DETAILS_INHERIT_MARKER}\n" + "\n\n".join(chunk for _, chunk in blocks)
    if skipped:
        text += f"\n\n[{skipped} earlier generation(s) omitted for length]"
    return text, len(blocks), skipped



# subject 机械闸门的开关。
#   True  = 召回后把 subject 规范化不相等的候选直接从名单里删掉，LLM 看不到，且
#           SUPERSEDE 执行前再复查一次。
#   False（默认）= 关掉这个闸门，覆盖判定只走语义召回 + LLM。
#
# 之所以做成开关：它的收益取决于 subject 本身是否稳定。
#   - subject 是稳定的实体名（人、文件、服务）且彼此并列时，闸门能防止跨主体误合并，
#     几乎不误伤。
#   - subject 是自由文本或位置标签时，同一实体会被写成不同的 subject，等值比较会把
#     真实的更新拦掉；如果后续步骤依赖前一步的结果，被拦掉的更新会让它读到过期值。
# 一个只在部分场景成立的机制不该当默认值，所以默认关闭。
#
# 关掉之后的已知代价：跨主体误合并会回来。补偿路径是 fact_chain_summary，它保留了
# 被覆盖事实的演变，但保真度随链距衰减。
_SUBJECT_GATE_DEFAULT = False
ENV_SUBJECT_GATE = "HY_FACT_SUBJECT_GATE"


def _subject_gate_default() -> bool:
    """环境变量兜底，方便不改代码就切换默认行为。

    未设置 → 用 `_SUBJECT_GATE_DEFAULT`。设为 0/false/no/off → 关闭。
    显式传给 `WorkingFactReconciler(subject_gate=...)` 的值优先于环境变量。
    """
    raw = os.getenv(ENV_SUBJECT_GATE)
    if raw is None or not raw.strip():
        return _SUBJECT_GATE_DEFAULT
    return raw.strip().lower() not in {"0", "false", "no", "off"}


def _subject_conflict(new_subject: str, old_subject: str) -> bool:
    """两条事实的主体是否明确不同（不同 → 禁止覆盖）。

    只在双方都声明了主体、且规范化后不相等时才算冲突。有一侧为空就返回 False：
    空不等于"主体不同"，它等于"这次没能定出主体"（抽取没给、兜底存的原文、或者
    改动前写进库的老行）。把空当成冲突会让老库里的事实永远无法被覆盖，那是拿一个
    未知去否决一个判断；交回 LLM 按语义判，才是改动前的行为。
    """
    a, b = normalize_subject(new_subject), normalize_subject(old_subject)
    return bool(a) and bool(b) and a != b



# ================================================================
# Prompt
# ================================================================
#
# The two subject rules below (a matching subject as a necessary condition for
# SUPERSEDE, and different subjects as a reason to keep both) are general: they
# are stated over persons, files/modules, and hosts/services alike, and they say
# what "successive" means for any memory store, not what any one dataset looks
# like.
#
# Avoid few-shot examples whose surface form resembles one specific dataset.
# General subject-isolation rules belong in the prompt; lookalike examples
# encourage pattern matching instead of teaching the rule.

from ..prompts.en import CODING_FACT_RECONCILE_PROMPT as FACT_RECONCILE_PROMPT  # noqa: E402,F401  (re-export)


from ..prompts.en import CODING_CHAIN_SUMMARY_PROMPT as CHAIN_SUMMARY_PROMPT  # noqa: E402,F401  (re-export)


def _fact_drafts_language_text(drafts: List[WorkingFactDraft]) -> str:
    parts = []
    for draft in drafts:
        # statement 是实际检索正文和本次会话语言的主来源；subject 经常是英文
        # 文件名/符号名，不能让它抢在中文 statement 前面决定 prompt 语言。
        parts.extend([draft.statement, draft.details, draft.subject])
    return "\n".join(str(part).strip() for part in parts if str(part).strip())


def build_fact_reconcile_prompt(
    drafts: List[WorkingFactDraft],
    candidates: List[List[WorkingFact]],
    language: Optional[str] = None,
) -> Tuple[str, str]:
    """Return the fact reconcile prompt and its language code.

    Explicit ``language`` wins; otherwise zh/en auto-detection over the drafts.
    """
    explicit = normalize_language(language)
    code = resolve_language(explicit, _fact_drafts_language_text(drafts))
    prompt = get_prompt("CODING_FACT_RECONCILE_PROMPT", code).format(
        drafts_block=WorkingFactReconciler._format_drafts(drafts),
        candidates_block=WorkingFactReconciler._format_candidates(drafts, candidates),
    )
    return with_output_language(prompt, code, explicit=explicit is not None), code


def build_chain_summary_prompt(
    chain_block: str,
    *,
    current_statement: str,
    language: Optional[str] = None,
) -> Tuple[str, str]:
    """Return the cross-session summary prompt and its language code."""
    explicit = normalize_language(language)
    code = resolve_language(explicit, current_statement or chain_block)
    prompt = get_prompt("CODING_CHAIN_SUMMARY_PROMPT", code).format(
        chain_block=chain_block,
    )
    return with_output_language(prompt, code, explicit=explicit is not None), code


@dataclass
class FactReconcilePlan:
    """plan() 的产物：覆盖判定已完成、尚未落库。apply() 才执行 store 操作。"""

    drafts: List[WorkingFactDraft]
    candidates: List[List[WorkingFact]] = field(default_factory=list)
    supersedes: Dict[int, List[str]] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)


# ================================================================
# WorkingFactReconciler
# ================================================================

class WorkingFactReconciler:
    """
    事实层 reconcile + 持久化执行器。

    一次调用：
      1. 每条 draft 用 statement embedding 召回 top-N active 事实
      2. 单次 LLM 决策哪些旧事实属于同一用户任务的前序 session
      3. 先处理 SUPERSEDE（软删 + 经验链过继），再 INSERT 新事实
      4. 真发生了覆盖的，再要一次 LLM 写跨 session 任务进展总结

    第 4 步只在有覆盖时触发，所以绝大多数写入不额外花钱。
    """

    def __init__(
        self,
        store: CodingMemoryStore,
        embed_service: EmbedService,
        llm_provider: LLMProvider,
        *,
        max_tokens: int = 1200,
        temperature: Optional[float] = None,
        candidates_per_draft: int = 5,
        enable_chain_summary: bool = True,
        subject_gate: Optional[bool] = None,
        inherit_details: Optional[bool] = None,
    ):
        self.store = store
        self.embed_service = embed_service
        self.llm_provider = llm_provider
        self.max_tokens = max_tokens
        # None → 复用 LLMConfig.temperature（避免硬编码触发 kimi/deepseek 的温度硬约束）
        if temperature is None:
            # None 一路传到 provider = 不带该参数；此前兜 0.1 会绕过用户配置，
            # 在只接受特定 temperature 的网关上（Opus 5 只收 1 或不传）直接 400
            temperature = _resolve_configured_temperature(llm_provider)
        self.temperature = temperature
        self.candidates_per_draft = candidates_per_draft
        # 关掉就不生成演变总结，省一次调用
        self.enable_chain_summary = enable_chain_summary
        # subject 机械闸门：None → 读环境变量 HY_FACT_SUBJECT_GATE，再退到默认值。
        # 关掉后覆盖判定只剩语义召回 + LLM，详见文件顶部 `_SUBJECT_GATE_DEFAULT` 的说明。
        self.subject_gate = (
            _subject_gate_default() if subject_gate is None else bool(subject_gate)
        )
        # 被覆盖事实的 details 过继：None → 读环境变量 HY_FACT_INHERIT_DETAILS，
        # 再退到默认值。详见 `_build_inherited_details` 上方的说明。
        self.inherit_details = (
            _inherit_details_default() if inherit_details is None else bool(inherit_details)
        )

    async def reconcile(
        self,
        drafts: List[WorkingFactDraft],
        *,
        user_id: str,
        agent_id: str = "default_agent",
        session_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        source: str = "default",
        memory_at: Optional[datetime] = None,
        language: Optional[str] = None,
    ) -> Tuple[List[WorkingFact], List[FactReconcileOp]]:
        """
        执行 reconcile + 持久化（plan() 后紧接 apply()）。memory_at 是这段对话的
        时间，记到新事实上。language 为显式 prompt 语言码（None → zh/en 自动检测），
        同时用于演变总结。

        Returns:
            (真正落库的新事实, 执行过的 ops)。

        Raises:
            CodingWriteError(RECONCILE_FAILED)：覆盖判定的 LLM 失败 / 空回复 /
                解析不出。此时既不插入新事实，也不覆盖任何旧事实。
        """
        plan = await self.plan(
            drafts,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            workspace_id=workspace_id,
            branch=branch,
            source=source,
            memory_at=memory_at,
            language=language,
        )
        return await self.apply(plan)

    async def plan(
        self,
        drafts: List[WorkingFactDraft],
        *,
        user_id: str,
        agent_id: str = "default_agent",
        session_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        source: str = "default",
        memory_at: Optional[datetime] = None,
        language: Optional[str] = None,
    ) -> FactReconcilePlan:
        """检索 candidates + 覆盖判定，不落库。

        没有任何候选时不调 LLM（全部 INSERT 是确定的结论，不是兜底）。调了 LLM 时，
        有效回复 = JSON 对象且 "ops" 是列表（空列表 = 都不覆盖、只插入）。LLM 调用
        失败（LLM_UNAVAILABLE）、200 但 content 为空（EMPTY_RESPONSE）、回复不是
        上述形状（JSON_PARSE_FAILED）时抛 CodingWriteError(RECONCILE_FAILED)。
        此前 LLM 失败会退化为全 INSERT 不覆盖，留下本该被接续的旧事实。
        """
        context = dict(
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            workspace_id=workspace_id,
            branch=branch,
            source=source,
            memory_at=memory_at,
            language=language,
        )
        if not drafts:
            return FactReconcilePlan(drafts=[], context=context)

        candidates = await self._retrieve_candidates(
            drafts,
            user_id=user_id,
            agent_id=agent_id,
            session_id=session_id,
            workspace_id=workspace_id,
            branch=branch,
            source=source,
        )

        try:
            supersedes = await self._llm_decide(drafts, candidates, language=language)
        except CodingWriteError:
            raise
        except Exception as e:
            logger.warning(f"[fact-reconcile] LLM decide failed: {e}")
            raise CodingWriteError(
                "RECONCILE_FAILED",
                f"working fact reconcile LLM call failed: {type(e).__name__}: {e}",
                cause="LLM_UNAVAILABLE",
            ) from e

        return FactReconcilePlan(
            drafts=list(drafts),
            candidates=candidates,
            supersedes=supersedes,
            context=context,
        )

    async def apply(
        self, plan: FactReconcilePlan,
    ) -> Tuple[List[WorkingFact], List[FactReconcileOp]]:
        """执行 plan() 的决策（覆盖旧事实 + 插入新事实）。"""
        if not plan.drafts:
            return [], []
        facts, ops = await self._execute(
            plan.drafts,
            plan.candidates,
            plan.supersedes,
            **plan.context,
        )
        logger.info(
            f"[fact-reconcile] drafts={len(plan.drafts)} inserted={len(facts)} "
            f"superseded={sum(1 for o in ops if o.action == 'SUPERSEDE')}"
        )
        return facts, ops

    # ------------------------------------------------------------
    # 检索 candidates
    # ------------------------------------------------------------

    async def _retrieve_candidates(
        self,
        drafts: List[WorkingFactDraft],
        *,
        user_id: str,
        agent_id: str,
        session_id: Optional[str],
        workspace_id: Optional[str],
        branch: Optional[str],
        source: str,
    ) -> List[List[WorkingFact]]:
        """每条 draft 用 statement 召回同 user 的 top-N active 事实。

        `self.subject_gate` 开启时（默认），召回后过 subject 闸门：主体不同的候选直接
        从名单里去掉，LLM 根本看不到，所以它连误判的机会都没有。这不是省一次调用的
        优化，是纠正一个真实缺陷——同一 workspace 下多个并列主体（多人共用一份行程、
        多机共用一套脚本）的 statement 语义高度相似，纯语义 + LLM 会把它们判成同一
        任务的先后版本。

        关掉时整份召回原样交给 LLM。适用于 subject 不是稳定实体名的数据集（自由文本、
        位置标签），那里精确字符串相等会把同实体判成不同主体，反而拦掉真实更新。
        """
        # Empty/legacy session metadata remains readable, but cannot prove
        # conversation authority for a destructive SUPERSEDE.
        if not session_id:
            return [[] for _ in drafts]
        try:
            embeddings = await self.embed_service.embed_batch(
                [d.statement for d in drafts]
            )
        except Exception as e:
            logger.warning(f"[fact-reconcile] embed_batch failed: {e}")
            return [[] for _ in drafts]

        result: List[List[WorkingFact]] = []
        for draft, emb in zip(drafts, embeddings):
            try:
                hits = await self.store.search_facts(
                    emb, user_id=user_id, top=self.candidates_per_draft,
                    agent_id=agent_id,
                    session_id=session_id,
                    workspace_id=workspace_id,
                    branch=branch,
                    # 写入侧同样接词面通道：候选选错了 LLM 就没有机会判对。这里不传
                    # min_score——写入侧要的是"有哪些可能的前身"，让 LLM 看更宽的名单，
                    # 阈值过滤是读取侧的事。
                    query_text=draft.statement,
                    include_sources=[source],
                )
            except Exception as e:
                logger.warning(f"[fact-reconcile] retrieve candidates failed: {e}")
                result.append([])
                continue
            if not self.subject_gate:
                result.append([fact for fact, _ in hits])
                continue
            kept, blocked = [], []
            for fact, _ in hits:
                if _subject_conflict(draft.subject, fact.subject):
                    blocked.append(fact)
                else:
                    kept.append(fact)
            if blocked:
                logger.info(
                    f"[fact-reconcile] subject gate blocked {len(blocked)} candidate(s): "
                    + ", ".join(f.fact_id for f in blocked)
                )
                logger.debug(
                    f"[fact-reconcile] subject gate: draft subject={draft.subject!r} "
                    f"blocked subjects={[f.subject for f in blocked]!r}"
                )
            result.append(kept)
        return result

    # ------------------------------------------------------------
    # LLM 决策
    # ------------------------------------------------------------

    async def _llm_decide(
        self,
        drafts: List[WorkingFactDraft],
        candidates: List[List[WorkingFact]],
        language: Optional[str] = None,
    ) -> Dict[int, List[str]]:
        """返回 {draft_idx: [被它覆盖的旧 fact_id, ...]}。"""
        # 一条候选都没有就没得判，省一次 LLM 调用
        if not any(candidates):
            return {}

        prompt, _ = build_fact_reconcile_prompt(drafts, candidates, language=language)
        resp = await self.llm_provider.complete(
            prompt=prompt,
            max_tokens=self.max_tokens,
            audit_stage="coding_fact_reconcile",
        )
        failure = self._reply_failure(resp.content)
        if failure is not None:
            code, message = failure
            logger.warning(f"[fact-reconcile] {code}: {message}")
            raise CodingWriteError("RECONCILE_FAILED", message, cause=code)
        return self._parse_ops(resp.content)

    # ------------------------------------------------------------
    # 演变总结
    # ------------------------------------------------------------

    async def _collect_history(
        self, retired: List[WorkingFact], *, user_id: str
    ) -> Tuple[List[WorkingFact], bool]:
        """摊平出"这个任务此前怎样推进"，时间正序，返回 (历代, 是否截断过)。

        历史不另存，就在库里：被覆盖的事实原样留着，fact_link 指向后继，反查
        一路走回去就是完整血统。retired 是这次刚被顶掉的那几条，各自往上追。

        追太深会把提示词撑爆，超过 MAX_FACT_CHAIN_ENTRIES 就丢最老的，并告诉调用方
        丢过，免得 LLM 以为这就是全部历史。只报"有没有丢"不报"丢了几代"：要数准
        得递归走完整条链，为一句提示词不值。多走一步就够判断有没有更早的了。
        """
        by_id: Dict[str, WorkingFact] = {}
        for old in retired:
            try:
                ancestors = await self.store.get_fact_ancestors(
                    old.fact_id, user_id=user_id,
                    max_depth=MAX_FACT_CHAIN_ENTRIES + 1,
                )
            except Exception as e:
                logger.warning(f"[fact-reconcile] walk ancestors failed: {e}")
                ancestors = []
            # 一次顶掉多条时几条血统会汇到一起，共同祖先按 fact_id 天然去重
            for f in ancestors:
                by_id[f.fact_id] = f
            by_id[old.fact_id] = old

        ordered = sorted(by_id.values(), key=lambda f: f.created_at or datetime.min)
        truncated = len(ordered) > MAX_FACT_CHAIN_ENTRIES
        return ordered[-MAX_FACT_CHAIN_ENTRIES:], truncated

    async def _summarize_chain(
        self, history: List[WorkingFact], current: str, *, truncated: bool = False,
        language: Optional[str] = None,
    ) -> str:
        """让 LLM 把同一任务的跨 session 进展写成简短摘要。

        失败返回空串，不抛。总结这一步不值得让整次写入失败，而且历史并没丢——
        它还在库里，下次这条事实再被覆盖时会重新走一遍链，总结自己就补回来了。
        """
        if not self.enable_chain_summary or not history:
            return ""

        rows = [
            f"({short_date(f.created_at.isoformat() if f.created_at else '')}) "
            f"{f.statement}".strip()
            for f in history
        ]
        if truncated:
            rows.insert(0, "(... older revisions omitted)")
        rows.append(f"(now) {current}")
        prompt, _ = build_chain_summary_prompt(
            "\n".join(rows),
            current_statement=current,
            language=language,
        )

        try:
            resp = await self.llm_provider.complete(
                prompt=prompt,
                max_tokens=_CHAIN_SUMMARY_MAX_TOKENS,
                audit_stage="coding_fact_summary",
            )
        except Exception as e:
            logger.warning(f"[fact-reconcile] chain summary failed: {e}")
            return ""

        return self._clean_summary(getattr(resp, "content", "") or "")

    @staticmethod
    def _clean_summary(text: str) -> str:
        """剥掉模型爱加的装饰：代码围栏、外层引号、"总结："之类的开场白。"""
        s = (text or "").strip()
        if s.startswith("```"):
            # 去掉首行 ``` 或 ```json，以及结尾围栏
            s = s.split("\n", 1)[-1] if "\n" in s else ""
            s = s.rsplit("```", 1)[0].strip()
        for prefix in ("总结：", "总结:", "Summary:", "summary:"):
            if s.startswith(prefix):
                s = s[len(prefix):].strip()
        # 只在整句被一对引号裹住时剥。句中还引了别的东西（路径、变量名）就别动，
        # 否则 `"config" 指向 "env"` 会被削成 `config" 指向 "env`。
        if len(s) >= 2 and s[0] in "\"'“「" and s[-1] in "\"'”」":
            inner = s[1:-1]
            if s[0] not in inner and s[-1] not in inner:
                s = inner.strip()
        # 模型偶尔会分行铺开，压成一行免得污染提示词排版
        return " ".join(s.split())

    @staticmethod
    def _format_drafts(drafts: List[WorkingFactDraft]) -> str:
        rows = [
            f"draft[{i}]: subject={d.subject!r} statement={d.statement!r}"
            if d.subject
            else f"draft[{i}]: subject=(unspecified) statement={d.statement!r}"
            for i, d in enumerate(drafts)
        ]
        return "\n".join(rows) if rows else "(none)"

    @staticmethod
    def _format_candidates(
        drafts: List[WorkingFactDraft],
        candidates: List[List[WorkingFact]],
    ) -> str:
        rows = []
        for i in range(len(drafts)):
            cands = candidates[i] if i < len(candidates) else []
            if not cands:
                rows.append(f"for draft[{i}]: (no candidates)")
                continue
            cand_rows = [
                f"  - fact_id={c.fact_id}\n"
                f"    subject: {repr(c.subject) if c.subject else '(unspecified)'}\n"
                f"    statement: {c.statement!r}\n"
                f"    created_at: {c.created_at.isoformat() if c.created_at else 'n/a'}"
                for c in cands
            ]
            rows.append(f"for draft[{i}]:\n" + "\n".join(cand_rows))
        return "\n\n".join(rows) if rows else "(none)"

    @classmethod
    def _reply_failure(cls, text: Optional[str]) -> Optional[Tuple[str, str]]:
        """回复本身是失败时返回 (cause, message)；有效回复（含 {"ops": []}）返回 None。"""
        if not (text or "").strip():
            return "EMPTY_RESPONSE", "working fact reconcile LLM returned empty response"
        obj = cls._load_object(text)
        if not isinstance(obj, dict) or not isinstance(obj.get("ops"), list):
            return (
                "JSON_PARSE_FAILED",
                f"working fact reconcile output has no ops list ({len(text)} chars)",
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
            body = [line for line in s.split("\n") if not line.startswith("```")]
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
    def _parse_ops(cls, text: str) -> Dict[int, List[str]]:
        """宽松解析 ops，返回 {draft_idx: [fact_id...]}。"""
        if not text:
            return {}
        obj = cls._load_object(text)
        if not isinstance(obj, dict):
            logger.warning(
                f"[fact-reconcile] failed to parse ops output ({len(text or '')} chars)"
            )
            logger.debug(f"[fact-reconcile] unparsable ops output: {text!r}")
            return {}
        ops_raw = obj.get("ops")
        if not isinstance(ops_raw, list):
            return {}

        result: Dict[int, List[str]] = {}
        for raw in ops_raw:
            if not isinstance(raw, dict):
                continue
            try:
                idx = int(raw.get("draft_idx"))
            except Exception:
                continue
            ids = raw.get("supersedes") or []
            if not isinstance(ids, list):
                continue
            clean = [str(x).strip() for x in ids if str(x).strip()]
            if clean:
                result.setdefault(idx, []).extend(clean)
        return result

    # ------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------

    async def _execute(
        self,
        drafts: List[WorkingFactDraft],
        candidates: List[List[WorkingFact]],
        supersedes: Dict[int, List[str]],
        *,
        user_id: str,
        agent_id: str,
        session_id: Optional[str],
        workspace_id: Optional[str],
        branch: Optional[str] = None,
        source: str = "default",
        memory_at: Optional[datetime] = None,
        language: Optional[str] = None,
    ) -> Tuple[List[WorkingFact], List[FactReconcileOp]]:
        inserted: List[WorkingFact] = []
        ops: List[FactReconcileOp] = []
        # 一条旧事实只能被覆盖一次，否则 fact_link 会指向后来的那条，链就断了
        already_superseded: set = set()

        for idx, draft in enumerate(drafts):
            new_fact = WorkingFact(
                fact_id=str(uuid.uuid4()),
                user_id=user_id,
                agent_id=agent_id,
                session_id=session_id,
                workspace_id=workspace_id,
                branch=branch,
                source=source,
                statement=draft.statement,
                files=list(draft.files),
                subject=draft.subject,
                details=draft.details,
                status="active",
                memory_at=memory_at,
            )

            # 先确认新事实能向量化，再去覆盖旧事实：否则 embedding 挂掉时旧事实
            # 已被软删、新事实又落不了库，这条结论就凭空没了。失败抛
            # CodingWriteError(EMBEDDING_FAILED)，整次写入报失败。
            new_embedding = await self.store.embed_fact(new_fact)

            allowed = {c.fact_id for c in (candidates[idx] if idx < len(candidates) else [])}
            targets = [
                fid for fid in supersedes.get(idx, [])
                if fid in allowed and fid not in already_superseded
            ]

            # 先过继经验链再软删旧事实，新事实入库时链已经是全的
            inherited: List[str] = []
            retired: List[WorkingFact] = []
            for old_id in targets:
                old = await self.store.get_fact(old_id, user_id=user_id)
                if old is None:
                    continue
                if (
                    old.agent_id != agent_id
                    or old.session_id != session_id
                    or old.workspace_id != workspace_id
                ):
                    logger.warning(
                        f"[fact-reconcile] refused cross-authority SUPERSEDE: {old.fact_id}"
                    )
                    continue
                # 闸门在候选阶段已经拦过一道，这里再拦一次：候选是按 statement 召回的，
                # 而 LLM 只能覆盖候选名单里的 id，理论上到不了这儿。真到了就说明前面
                # 哪一环漏了，宁可让这次覆盖不生效——多留一条旧事实是可恢复的，删掉
                # 别的主体的状态不是。闸门关掉时这道复查一并跳过，否则闸门就没关干净。
                if self.subject_gate and _subject_conflict(draft.subject, old.subject):
                    logger.warning(
                        f"[fact-reconcile] refused cross-subject SUPERSEDE: {old.fact_id}"
                    )
                    logger.debug(
                        f"[fact-reconcile] cross-subject: old subject={old.subject!r} "
                        f"draft subject={draft.subject!r}"
                    )
                    continue
                try:
                    ok = await self.store.supersede_fact(old_id, new_fact.fact_id)
                except Exception as e:
                    logger.warning(f"[fact-reconcile] SUPERSEDE failed: {e}")
                    continue
                if not ok:
                    continue
                already_superseded.add(old_id)
                for eid in old.derived_experience_ids:
                    if eid not in inherited:
                        inherited.append(eid)
                retired.append(old)
                ops.append(FactReconcileOp(
                    action="SUPERSEDE",
                    draft_idx=idx,
                    fact_id=old_id,
                    superseded_by=new_fact.fact_id,
                ))

            new_fact.derived_experience_ids = inherited
            if retired:
                history, truncated = await self._collect_history(
                    retired, user_id=user_id
                )
                new_fact.fact_chain_summary = await self._summarize_chain(
                    history, new_fact.statement, truncated=truncated,
                    language=language,
                )
                # 摘要之外再把历代自己的 details 原样过继一份。摘要保叙事，过继保数值，
                # 两者不重复：details 本来就不进摘要（_collect_history 只读 statement）。
                if self.inherit_details:
                    block, taken, skipped = _build_inherited_details(history)
                    if block:
                        new_fact.details = (
                            f"{new_fact.details}\n\n{block}".strip()
                            if new_fact.details
                            else block
                        )
                        logger.info(
                            f"[fact-reconcile] inherited details from {taken} generation(s)"
                            + (f", {skipped} skipped for length" if skipped else "")
                            + f" into {new_fact.fact_id}"
                        )

            try:
                await self.store.insert_fact(new_fact, embedding=new_embedding)
            except CodingWriteError:
                raise
            except Exception as e:
                logger.warning(f"[fact-reconcile] INSERT failed: {e}")
                continue

            # 过继来的经验反向补上新事实，两边链保持对称
            for eid in inherited:
                await self._append_source_fact(eid, new_fact.fact_id, user_id=user_id)

            inserted.append(new_fact)
            ops.append(FactReconcileOp(
                action="INSERT", draft_idx=idx, fact_id=new_fact.fact_id
            ))

        return inserted, ops

    async def _append_source_fact(
        self, experience_id: str, fact_id: str, *, user_id: str
    ) -> None:
        try:
            exp = await self.store.get_by_id(experience_id, user_id=user_id)
            if exp is None:
                # 经验已被 DELETE，死链交给检索时的存在性过滤兜底
                return
            if fact_id in exp.source_fact_ids:
                return
            await self.store.set_experience_source_facts(
                experience_id, list(exp.source_fact_ids) + [fact_id]
            )
        except Exception as e:
            logger.warning(f"[fact-reconcile] backfill source_fact_ids failed: {e}")
