# -*- coding: utf-8 -*-
"""
Coding Memory - Extractor

一次 LLM 调用，对同一段轨迹产出双层草稿：

- 经验层 experiences（有价值才抽）：task / search_keys / solution /
  boundary_envs / confidence。价值门槛严格，rename a file 这种琐碎操作不该成
  memory；只抽取未来可能复发的有价值问题。
- 事实层 fact（模型可返回 0 或 1 条）：statement / files。statement 是这次会话的
  小结——用户要做什么 / 大致怎么做的 / 结果如何。模型未产出时是否存原文 fallback
  由 `MEMORY_CODING_VERBATIM_FALLBACK_ENABLED` 控制，默认关闭。

复用 LLMConfig.model（与 chat extractor 共用同一 LLM 配置）。
"""

import json
import logging
import time
from typing import List, Optional, Dict, Any, Tuple

from ..agent.llm_provider import LLMProvider
from ..pipelines.base import ChatMessage
from ..prompts import (
    get_prompt,
    normalize_language,
    resolve_language,
    with_output_language,
)
from .preproc import (
    extract_files,
    keep_last_k_user_turns,
    prepare_extractor_messages,
)
from .judge import _store_coding_log
from .types import CodingMemoryDraft, CodingWriteError, WorkingFactDraft



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


# subject 是等值比较的硬键，一句话说明抽取跑偏了，按"没抽到主体"处理。
_MAX_SUBJECT_CHARS = 80
# details 存原文，要装得下一份完整计划表 / 配置块，但不能让单条事实吃掉整个提示词。
_MAX_DETAILS_CHARS = 4000
# 兜底 statement 是检索向量，太长会被无关内容稀释；一两段用户原话足够。
# 同一个理由也约束正常路径的长度：提示词把 statement 的软上限定在 1200 字符左右，
# 靠"给定量写成数据而非对话原句"来达到，不靠砍覆盖 —— details 不进检索向量，
# statement 是检索命中后最主要的可读字段。
_VERBATIM_STATEMENT_CHARS = 600


# ================================================================
# Prompt
# ================================================================

from ..prompts.en import CODING_EXTRACT_PROMPT as EXTRACT_PROMPT  # noqa: E402,F401  (re-export)


# ================================================================
# Prompt —— scene="coding"（软件工程场景）
# ================================================================
# 与上面 EXTRACT_PROMPT(scene="working") 的关系: 同一个抽取器、同一套占位符
# ({workspace_present}/{branch_present}/{conversation_block})、同一个输出 schema,
# 只换规范。两份并存而不是改一份, 是因为两个场景对 statement 的要求不同,
# 共用一份会让其中一个场景的规范被另一个稀释。
#
# 这份独有的三块(working 侧没有):
#   1. HARD CONSTRAINT 头部 —— 前置覆盖后文任何"想要叙事"的措辞: 语法主语必须是
#      CODE 或 DECISION 而非 "the user"; 不许以 "the session was completed" 收尾;
#      必须保留具体症状(报错串/失败命令/错误输出), 那是未来检索的命中面。
#   2. CODE WORK 整节 —— 四件未来 agent 无法从代码反推的东西: 分支名(逐字)、
#      哪一版赢了(settled on A; interim B dropped)、精确坐标(仓相对路径+符号,
#      不是贴代码)、verified 还是仅 written。它明确不要求更长, 要求同样预算花在
#      更高价值的 token 上。
#   3. statement 的三段式 —— OPEN 症状(用户会怎么报) → THEN 坐标+决策 →
#      CLOSE 证据状态。硬顶 ~400 latin 字符。
#
# 这份少掉的(working 侧有、coding 侧刻意不带):
#   · "问用户"排除段 —— 卡在向用户要参数时强制 experiences: []。coding 场景的
#     轨迹是 agent 自主执行, 不存在这种停顿。
#   · statement PART 2 的 TASK-SCOPED GIVENS 逐行记法 —— coding 侧的给定量是
#     代码坐标, 已由 CODE WORK 节覆盖。
#   · details 的产物复制规范 —— coding 侧 details 不进检索向量也不注入。
#
# 修改 prompt 时应使用代表性 coding traces 做回归，确认坐标、决策与验证状态仍保留。

from ..prompts.en import CODING_EXTRACT_PROMPT_CODING as EXTRACT_PROMPT_CODING  # noqa: E402,F401  (re-export)


# ================================================================
# CodingMemoryExtractor
# ================================================================

class CodingMemoryExtractor:
    """
    LLM 驱动的双层抽取器。

    输入：本次完整 New Messages + 历史最近 K 个 user-turn；两部分的 LLM 视图均限长
    输出：(List[CodingMemoryDraft], List[WorkingFactDraft])

    事实层一段轨迹只产一条，所以返回的 fact 列表长度恒为 0 或 1；用列表而不用
    Optional 是为了让 writer / reconciler 的签名不必跟着改。
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        max_tokens: int = 6000,
        temperature: Optional[float] = None,
        cache: Any = None,
        scene: str = "working",
        verbatim_fallback_enabled: bool = False,
    ):
        # scene 决定用哪份抽取规范。直接构造 extractor 时默认 working；client 的
        # scene=None 自动路由会先经三分类 judge，再构造对应 variant。
        #   "working" → EXTRACT_PROMPT        （办公 / 通用生产力）
        #   "coding"  → EXTRACT_PROMPT_CODING （软件工程）
        # 非法值不抛错、退回 working：抽取是一次性的，为一个拼错的参数让整段
        # 写入失败不值得。
        self.scene = "coding" if str(scene or "").strip().lower() == "coding" else "working"
        self._prompt_tmpl = (
            EXTRACT_PROMPT_CODING if self.scene == "coding" else EXTRACT_PROMPT
        )
        self.llm_provider = llm_provider
        self.max_tokens = max_tokens
        # 默认关闭：原文兜底可能把 assistant/tool 轨迹直接写进 statement，随后被
        # 搜索消费者当作记忆正文展示。需要旧行为时由 CodingConfig 显式开启。
        self.verbatim_fallback_enabled = bool(verbatim_fallback_enabled)
        # cache 非空时落 CODING_EXTRACT pipeline log（best-effort）
        self.cache = cache
        # None → 使用 LLMConfig.temperature（避免硬编码触发 kimi/deepseek 的温度硬约束）
        if temperature is None:
            # None 一路传到 provider = 不带该参数；此前兜 0.1 会绕过用户配置，
            # 在只接受特定 temperature 的网关上（Opus 5 只收 1 或不传）直接 400
            temperature = _resolve_configured_temperature(llm_provider)
        self.temperature = temperature

    @staticmethod
    def _language_text(messages: List[ChatMessage]) -> str:
        """Use new user text for routing; tool output must not dominate it."""
        user_parts = [
            str(message.content or "").strip()
            for message in messages
            if message.role == "user" and str(message.content or "").strip()
        ]
        if user_parts:
            return "\n".join(user_parts)

        non_tool_parts = [
            str(message.content or "").strip()
            for message in messages
            if message.role != "tool" and str(message.content or "").strip()
        ]
        if non_tool_parts:
            return "\n".join(non_tool_parts)
        return "\n".join(
            str(message.content or "").strip()
            for message in messages
            if str(message.content or "").strip()
        )

    def _select_prompt_template(
        self, messages: List[ChatMessage], language: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Return (template, language code) for this write's new messages.

        Explicit ``language`` wins; otherwise zh/en auto-detection over the new
        user text (unchanged behaviour).
        """
        code = resolve_language(language, self._language_text(messages))
        if code == "en":
            return self._prompt_tmpl, code
        name = (
            "CODING_EXTRACT_PROMPT_CODING" if self.scene == "coding"
            else "CODING_EXTRACT_PROMPT"
        )
        return get_prompt(name, code), code

    async def extract(
        self,
        messages: List[ChatMessage],
        *,
        history_messages: Optional[List[ChatMessage]] = None,
        tool_result_max_bytes: int = 2048,
        user_id: str,
        agent_id: str = "default_agent",
        workspace_id: Optional[str] = None,
        branch: Optional[str] = None,
        session_id: Optional[str] = None,
        request_id: str = "",
        language: Optional[str] = None,
    ) -> Tuple[List[CodingMemoryDraft], List[WorkingFactDraft]]:
        """
        Args:
            messages: 本次 write 的完整新消息；只做单条限长，不裁 turn
            history_messages: 之前 write 的历史消息；仅取最近 5 个 user-turn 作消解上下文
            user_id / agent_id / workspace_id / branch / session_id: SDK 注入
            language: 显式 prompt 语言码；None 时按新 user 文本做 zh/en 自动检测

        Returns:
            (经验草稿, 事实草稿)。两层都可能为空。只有显式启用
            verbatim_fallback_enabled 时，模型没产出 fact 才调用 _verbatim_fact。

        Raises:
            CodingWriteError: 未开启原文兜底时，LLM 调用失败（LLM_ERROR）、返回空
                content（EMPTY_RESPONSE）或回复解析不出 JSON 对象（JSON_PARSE_FAILED）。
        """
        if not messages:
            return [], []

        # New Messages 是本次 write 的抽取主体：无论传入 1 个还是 10 个 user-turn，
        # 都必须完整进入 prompt；这里只限制每条 tool / assistant 的体积。
        new_messages = prepare_extractor_messages(
            messages, tool_max_bytes=tool_result_max_bytes,
        )

        # Last 5 User Turns 是之前 write 的历史，只用于指代消解和上下文。绝不能拿
        # last-K 去裁本次输入，否则 write(10 turns) 会静默丢掉前 5 个 turn。
        history_window = keep_last_k_user_turns(
            prepare_extractor_messages(
                history_messages or [], tool_max_bytes=tool_result_max_bytes,
            ),
            k=5,
        )

        history_block = self._format_conversation(history_window).strip()
        new_block = self._format_conversation(new_messages).strip()
        explicit_language = normalize_language(language)
        prompt_tmpl, prompt_language = self._select_prompt_template(
            new_messages, explicit_language,
        )
        empty_block = get_prompt("CODING_EMPTY_BLOCK", prompt_language)
        conversation_block = get_prompt(
            "CODING_CONVERSATION_BLOCK", prompt_language,
        ).format(
            history=history_block or empty_block,
            new=new_block or empty_block,
        )

        prompt = prompt_tmpl.format(
            workspace_present=str(bool(workspace_id)).lower(),
            branch_present=str(bool(branch)).lower(),
            conversation_block=conversation_block,
        )
        prompt = with_output_language(
            prompt, prompt_language, explicit=explicit_language is not None,
        )

        _t0 = time.perf_counter()
        try:
            resp = await self.llm_provider.complete(
                prompt=prompt,
                max_tokens=self.max_tokens,
                audit_stage="coding_extract",
            )
        except Exception as e:
            logger.warning(f"[coding-extract] LLM call failed: {e}")
            # 经验层不兜底。事实层原文兜底默认关闭，避免 assistant/tool 轨迹被
            # 当作 statement 直接展示；关闭时 LLM 失败抛 CodingWriteError(LLM_ERROR)，
            # 整次写入报失败，而不是「成功、0 条」。仅在配置显式开启时保留旧行为。
            verbatim = (
                self._verbatim_fact(messages)
                if self.verbatim_fallback_enabled else None
            )
            fact_source = (
                "verbatim_fallback" if verbatim
                else "verbatim_fallback_disabled"
                if not self.verbatim_fallback_enabled
                else "none"
            )
            await _store_coding_log(
                self.cache, request_id=request_id, user_id=user_id, agent_id=agent_id,
                session_id=session_id or "", step="CODING_EXTRACT", prompt=prompt,
                response=f"[LLM error] {type(e).__name__}: {str(e)[:300]}",
                parsed=json.dumps(
                    {
                        "drafts": 0,
                        "llm_error": True,
                        "facts": [verbatim.to_dict()] if verbatim else [],
                        "fact_count": 1 if verbatim else 0,
                        "fact_source": fact_source,
                    },
                    ensure_ascii=False,
                ),
                elapsed_ms=round((time.perf_counter() - _t0) * 1000, 2),
            )
            if not self.verbatim_fallback_enabled:
                raise CodingWriteError(
                    "LLM_ERROR",
                    f"coding extract LLM call failed: {type(e).__name__}: {e}",
                ) from e
            return [], ([verbatim] if verbatim else [])
        _elapsed = round((time.perf_counter() - _t0) * 1000, 2)

        # 模型答了且没有可记的内容（如 {"experiences": [], "fact": null}）是正常的空
        # 抽取；200 但 content 为空、或回复解析不出 JSON 对象，是模型失败，不能当成
        # 「成功、0 条」。原文兜底显式开启时沿用旧行为（交给下面的 verbatim 兜底）。
        failure = self._output_failure(resp.content)
        if failure is not None and not self.verbatim_fallback_enabled:
            code, message = failure
            await _store_coding_log(
                self.cache, request_id=request_id, user_id=user_id, agent_id=agent_id,
                session_id=session_id or "", step="CODING_EXTRACT", prompt=prompt,
                response=resp.content or "",
                parsed=json.dumps(
                    {"drafts": 0, "error": code, "message": message},
                    ensure_ascii=False,
                ),
                elapsed_ms=_elapsed, prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens, total_tokens=resp.tokens_used,
            )
            raise CodingWriteError(code, message)

        raw_experiences, raw_facts = self._parse_output(resp.content)

        # 经验层的 files 走规则抽取（整段轨迹碰过的文件）；事实层的 files 由 LLM
        # 给，它挑的是"这次真正动过/查过的"，比规则抽取的全集更贴会话小结
        files = extract_files(messages)

        drafts: List[CodingMemoryDraft] = []
        for raw in raw_experiences:
            d = self._normalize_draft(
                raw,
                user_id=user_id,
                agent_id=agent_id,
                workspace_id=workspace_id,
                branch=branch,
                session_id=session_id,
                files=files,
            )
            if d is not None:
                drafts.append(d)

        fact_drafts: List[WorkingFactDraft] = []
        for raw in raw_facts:
            f = self._normalize_fact_draft(raw)
            if f is not None:
                fact_drafts.append(f)

        # details 不信 LLM 的抄写，改从未压缩的 messages 直接取。
        #
        # 抽取提示词里的对话是窗口化、截断过的（见 preproc），长 assistant 文本的
        # 中段在建 prompt 时已经不在了。让模型"逐字抄写"一段它看不到的原文，只能
        # 抄出带省略标记的头尾。
        #
        # details 的意义是留住被摘要丢掉的可寻址产物（计划表、配置块等）。它不进
        # 检索向量（见 store._upsert_fact_vec），长度不影响召回，没有理由再走一遍
        # 有损通道——直接取原文，只受 details 自己的上限约束。
        #
        # 注意：绕过一个有损环节不等于修好它。输入侧的截断阈值同样会影响 statement，
        # 见 preproc.DEFAULT_ASSISTANT_HEAD_CHARS。
        #
        # LLM 给的 details 仍然留着当兜底：messages 为空这种情况下它是唯一来源。
        verbatim_details = self._verbatim_details(messages)
        if verbatim_details:
            fact_drafts = [
                WorkingFactDraft(
                    statement=f.statement,
                    files=f.files,
                    subject=f.subject,
                    details=verbatim_details,
                )
                for f in fact_drafts
            ]

        fact_source = "llm"
        if not fact_drafts:
            if self.verbatim_fallback_enabled:
                verbatim = self._verbatim_fact(messages)
                if verbatim is not None:
                    fact_drafts.append(verbatim)
                    fact_source = "verbatim_fallback"
                else:
                    fact_source = "none"
            else:
                fact_source = "verbatim_fallback_disabled"

        logger.info(
            f"[coding-extract] produced {len(drafts)} experience draft(s), "
            f"{'1 session fact' if fact_drafts else 'no session fact'}"
            f"{' (verbatim fallback: LLM returned none)' if fact_source == 'verbatim_fallback' else ''}"
            f"{' (verbatim fallback disabled)' if fact_source == 'verbatim_fallback_disabled' else ''}"
            f" (workspace={workspace_id!r}, branch={branch!r})"
        )
        # CODING_EXTRACT：无论产出几条 draft（含 0 条）都落一条，便于排查"没抽到"。
        # 双层架构下 parsed 同时记录经验草稿与事实草稿，方便追查两层各自产出。
        await _store_coding_log(
            self.cache, request_id=request_id, user_id=user_id, agent_id=agent_id,
            session_id=session_id or "", step="CODING_EXTRACT", prompt=prompt,
            response=resp.content or "",
            parsed=json.dumps(
                {
                    "experiences": [d.to_dict() for d in drafts],
                    "experience_count": len(drafts),
                    "facts": [f.to_dict() for f in fact_drafts],
                    "fact_count": len(fact_drafts),
                    "fact_source": fact_source,
                },
                ensure_ascii=False, default=str,
            ),
            elapsed_ms=_elapsed, prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens, total_tokens=resp.tokens_used,
        )
        return drafts, fact_drafts

    # ------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------

    @classmethod
    def _verbatim_details(cls, messages: List[ChatMessage]) -> str:
        """整段轨迹的原文，作为 details 的权威来源。

        取的是未经 keep_last_k_user_turns 压缩的 messages：details 存在的理由就是
        留住摘要会丢掉的可寻址产物（第 N 天第 M 餐这种格子），而压缩正是丢它的那一步。
        它不进检索向量（store._upsert_fact_vec 只 embed subject + statement），所以
        长度只影响存储，不影响召回质量，唯一的上限是 _MAX_DETAILS_CHARS。

        从尾部截断：头部是结构化产物的开头（Day 1、配置首行），信息密度最高。
        """
        if not messages:
            return ""
        body = cls._format_conversation(messages).strip()
        if not body:
            return ""
        if len(body) <= _MAX_DETAILS_CHARS:
            return body
        return body[:_MAX_DETAILS_CHARS] + "\n…(truncated)"

    @classmethod
    def _verbatim_fact(
        cls, messages: List[ChatMessage]
    ) -> Optional[WorkingFactDraft]:
        """LLM 没给 fact 时的兜底：把轨迹原文存成一条事实。

        只在真的一个字都没有时返回 None（空消息、全空内容）。这是最后一道闸门，
        判据必须是"有没有内容"，不能是"内容够不够好"——够不够好是上面那次 LLM
        调用的判断，它已经判错过了，兜底再复读一遍同样的判断就没有意义。

        statement 用于检索，所以取用户说的话（用户的话就是未来查询的措辞，
        assistant 的复述和工具输出不是）；details 放整段原文，供命中后读取。
        subject 留空——主体只能靠语义判断，规则抽不出来，空串会让 reconciler 退回
        改动前的纯语义判覆盖，而不是拿一个猜错的锚点去卡。
        """
        if not messages:
            return None

        user_texts: List[str] = []
        for m in messages:
            if m.role == "user" and not m.is_tool_message():
                text = (m.content or "").strip()
                if text:
                    user_texts.append(text)

        details = cls._verbatim_details(messages)
        if not details:
            return None

        # 用户原话优先，一句都没有（纯工具回放）就退回整段轨迹的开头
        head = "\n".join(user_texts).strip() or details
        statement = head[:_VERBATIM_STATEMENT_CHARS]
        if len(head) > _VERBATIM_STATEMENT_CHARS:
            statement += "…"

        return WorkingFactDraft(statement=statement, files=[], subject="", details=details)

    @staticmethod
    def _format_conversation(messages: List[ChatMessage]) -> str:
        """渲染整段对话给 LLM 读。"""
        rows: List[str] = []
        for i, m in enumerate(messages):
            if m.role == "user" and not m.is_tool_message():
                rows.append(f"[{i}] user: {m.content}")
            elif m.role == "assistant":
                line = f"[{i}] assistant: {m.content}"
                if m.tool_calls:
                    tc_summary = "; ".join(
                        f"{tc.name}({_compact_args(tc.arguments)})"
                        for tc in m.tool_calls
                    )
                    line += f"  [tools: {tc_summary}]"
                rows.append(line)
            elif m.is_tool_message():
                tn = m.tool_name or "?"
                rows.append(f"[{i}] tool({tn}): {m.content}")
            elif m.role == "system":
                rows.append(f"[{i}] system: {m.content}")
            else:
                rows.append(f"[{i}] {m.role}: {m.content}")
        return "\n".join(rows)

    @classmethod
    def _output_failure(cls, text: Optional[str]) -> Optional[Tuple[str, str]]:
        """回复本身是失败时返回 (error_code, message)；正常（含空抽取）返回 None。"""
        if not (text or "").strip():
            return "EMPTY_RESPONSE", "coding extract LLM returned empty response"
        if cls._load_output_object(text) is None:
            return (
                "JSON_PARSE_FAILED",
                f"coding extract output is not a JSON object ({len(text)} chars)",
            )
        return None

    @staticmethod
    def _load_output_object(text: Optional[str]) -> Optional[Dict[str, Any]]:
        """宽松解析 LLM 输出，返回顶层 JSON 对象；解析不出返回 None。"""
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

        obj = None
        # 尝试纯 JSON
        try:
            obj = json.loads(s)
        except Exception:
            # 找第一个 {...} 块
            start = s.find("{")
            end = s.rfind("}")
            if start != -1 and end > start:
                try:
                    obj = json.loads(s[start: end + 1])
                except Exception:
                    obj = None
        return obj if isinstance(obj, dict) else None

    @classmethod
    def _parse_output(
        cls, text: str
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """宽松解析 LLM 输出 JSON，返回 (experiences, facts) 两个原始列表。"""
        if not text:
            return [], []
        obj = cls._load_output_object(text)
        if obj is None:
            logger.warning(
                f"[coding-extract] failed to parse output ({len(text or '')} chars)"
            )
            logger.debug(f"[coding-extract] unparsable output: {text!r}")
            return [], []

        # "memories" 是双层化之前的 key，老 prompt 缓存 / 旧模型可能还这么回
        experiences = cls._as_dict_list(obj.get("experiences"))
        if not experiences:
            experiences = cls._as_dict_list(obj.get("memories"))
        return experiences, cls._as_fact_list(obj)

    @classmethod
    def _as_fact_list(cls, obj: Dict[str, Any]) -> List[Dict[str, Any]]:
        """取出这一段的唯一事实，返回长度 0 或 1 的列表。

        现契约是单个 "fact" 对象。"facts" 数组是一段轨迹多条事实那版的形状，老
        模型和缓存住的老 prompt 还会这么回，所以继续认，但只留第一条——多出来的
        本来就不该存在，丢掉比让它们各自成为一条会话记录更接近本意。
        """
        fact = obj.get("fact")
        if isinstance(fact, dict):
            return [fact]

        legacy = cls._as_dict_list(obj.get("facts"))
        if len(legacy) > 1:
            logger.warning(
                f"[coding-extract] legacy 'facts' array with {len(legacy)} entries; "
                f"keeping the first, dropping {len(legacy) - 1}"
            )
        return legacy[:1]

    @staticmethod
    def _as_dict_list(value: Any) -> List[Dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [v for v in value if isinstance(v, dict)]

    @staticmethod
    def _normalize_draft(
        raw: Dict[str, Any],
        *,
        user_id: str,
        agent_id: str,
        workspace_id: Optional[str],
        branch: Optional[str],
        session_id: Optional[str],
        files: List[str],
    ) -> Optional[CodingMemoryDraft]:
        """
        把 LLM 输出的单条 memory 字典规范化为 CodingMemoryDraft。

        返回 None 表示该条无效（缺 task 或 solution）。
        """
        task = (raw.get("task") or "").strip()
        solution = (raw.get("solution") or "").strip()
        if not task or not solution:
            logger.debug(f"[coding-extract] drop draft missing task/solution: {raw}")
            return None

        # search_keys 规范化
        sk_raw = raw.get("search_keys") or []
        if not isinstance(sk_raw, list):
            sk_raw = []
        search_keys = [str(s).strip() for s in sk_raw if str(s).strip()]
        # 去重 + 与 task 同名的 key 去掉
        seen = set()
        deduped = []
        for k in search_keys:
            if k == task:
                continue
            if k in seen:
                continue
            seen.add(k)
            deduped.append(k)
        search_keys = deduped[:5]  # 软上限 5

        # confidence 规范化
        try:
            confidence = float(raw.get("confidence", 0.7))
        except Exception:
            confidence = 0.7
        confidence = max(0.0, min(1.0, confidence))

        boundary_envs = (raw.get("boundary_envs") or "").strip()

        return CodingMemoryDraft(
            task=task,
            search_keys=search_keys,
            solution=solution,
            boundary_envs=boundary_envs,
            confidence=confidence,
            user_id=user_id,
            agent_id=agent_id,
            workspace_id=workspace_id,
            branch=branch,
            session_id=session_id,
            files=list(files),
            extraction_source="auto_extract",
        )

    @staticmethod
    def _normalize_fact_draft(raw: Dict[str, Any]) -> Optional[WorkingFactDraft]:
        """把 LLM 输出的单条 fact 规范化。返回 None 表示 statement 为空。"""
        statement = (raw.get("statement") or "").strip()
        if not statement:
            logger.debug(f"[coding-extract] drop fact missing statement: {raw}")
            return None

        files_raw = raw.get("files") or []
        if not isinstance(files_raw, list):
            files_raw = []
        seen, files = set(), []
        for f in files_raw:
            f = str(f).strip()
            if f and f not in seen:
                seen.add(f)
                files.append(f)

        subject = " ".join(str(raw.get("subject") or "").split())
        # subject 是硬键而不是散文，一句话说明整段跑偏了，宁可当"没定出主体"处理
        # （退回纯语义判覆盖）也不要拿一句话去比对相等。
        if len(subject) > _MAX_SUBJECT_CHARS:
            logger.debug(
                f"[coding-extract] subject too long, treated as absent: {subject!r}"
            )
            subject = ""

        details = str(raw.get("details") or "").strip()
        if len(details) > _MAX_DETAILS_CHARS:
            # 从尾部截，头部是结构化产物的开头（Day 1、配置首行），信息密度最高
            details = details[:_MAX_DETAILS_CHARS] + "\n…(truncated)"

        return WorkingFactDraft(
            statement=statement, files=files, subject=subject, details=details
        )


# ================================================================
# Helpers
# ================================================================

def _compact_args(args: Dict[str, Any], max_chars: int = 600) -> str:
    """单行展示 tool args，并为关键命令参数保留足够证据窗口。

    仍设上限，避免整段脚本/大 payload 撑爆 prompt；但 80 字符会在常见 PowerShell
    命令的 ``-Depth`` / ``-ErrorAction`` 等参数前截断，与 fact 的证据忠实度契约冲突。
    """
    if not args:
        return ""
    try:
        s = json.dumps(args, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        s = str(args)
    if len(s) > max_chars:
        s = s[: max_chars - 3] + "..."
    return s
