"""
Agent Memory - Extractor 提取智能体

统一 Prompt 提取: 单次 LLM 调用提取 memory / intentions / basic_info。
  - memory: 一切值得长期记忆的信息（偏好/观点/事件/计划/经历/习惯/决定）
  - intentions: 用户未来要做的前瞻意图（带 valid_until 截止日）
  - basic_info: 结构化基础画像 kv

LLM 只需感知 memory 与 intention 两类，无需感知内部分层（layer）。

另保留 deep_extract (System 2 深度分析) 供后台精炼使用。
"""

from typing import Dict, Any, Optional, List
from dataclasses import dataclass, field
from enum import Enum
import json
import logging
import re

from .llm_provider import LLMProvider, _content_fingerprint, _log_llm_content_enabled
from ..config import LLMConfig as GlobalLLMConfig
from ..prompts import (
    get_prompt,
    normalize_language,
    resolve_language,
    with_output_language,
)

logger = logging.getLogger(__name__)


def _sentence_parts_for_dedup(text: str) -> List[str]:
    """Split complete sentences without breaking initials in personal names."""
    return [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+(?=[A-Z])", str(text or "").strip())
        if part.strip()
    ]


def _content_words_for_dedup(text: str) -> set:
    stop = {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for",
        "is", "was", "were", "has", "have", "had", "he", "she", "they", "it",
        "his", "her", "their", "user",
    }
    return {
        word for word in re.findall(r"[a-z0-9]+", str(text or "").casefold())
        if len(word) > 2 and word not in stop
    }


def dedup_internal_memory_sentences(text: str) -> str:
    """Drop only later complete sentences that are near-contained paraphrases."""
    parts = _sentence_parts_for_dedup(text)
    if len(parts) < 2:
        return text
    keep = [True] * len(parts)
    words = [_content_words_for_dedup(part) for part in parts]
    for later in range(1, len(parts)):
        short = words[later]
        if len(short) < 8:
            continue
        for earlier in range(later):
            long = words[earlier]
            if not long:
                continue
            coverage = len(short & long) / len(short)
            jaccard = len(short & long) / max(1, len(short | long))
            if coverage >= 0.75 and jaccard >= 0.45:
                keep[later] = False
                break
    return " ".join(part for part, include in zip(parts, keep) if include)


# ================================================================
# 数据结构
# ================================================================


class ExtractMode(str, Enum):
    """提取模式"""

    LIGHT = "light"  # System 1 轻量提取
    DEEP = "deep"  # System 2 深度提取


@dataclass
class ExtractResult:
    """提取结果 (V1 兼容)"""

    success: bool
    info: Dict[str, Any] = field(default_factory=dict)
    suggested_layer: Optional[str] = None
    confidence: float = 0.0
    tokens_used: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: Optional[str] = None
    error_code: Optional[str] = (
        None  # "EMPTY_RESPONSE" / "JSON_PARSE_FAILED" / "LLM_ERROR"
    )
    raw_response: Optional[str] = (
        None  # LLM 原始返回（失败时保留，用于写 pipeline_log）
    )
    llm_finish_reason: str = ""
    llm_model: str = ""
    llm_response_id: str = ""
    llm_choices_count: int = 0
    llm_content_len: int = 0
    llm_tool_calls_count: int = 0
    # 推理模型诊断：reasoning_tokens 接近/等于 max_tokens 且 content 为空，
    # 说明思维链吃光了输出配额（hy3 需传 reasoning_effort=no_think）。
    llm_reasoning_tokens: int = 0
    llm_reasoning_len: int = 0
    tool_calls_only: bool = False  # True 当 LLM 只返回了 tool_calls，content 为空
    attempts: List[Dict[str, Any]] = field(
        default_factory=list
    )  # LLM 重试各次失败明细（来自 LLMRetryError.attempts），写入 EXTRACT trace
    _actual_prompt: Optional[str] = (
        None  # 实际发送给 LLM 的 user prompt（用于 trace 日志）
    )
    _actual_system_prompt: Optional[str] = None  # 实际的 system prompt


@dataclass
class V2ExtractResult:
    """V2 提取结果 (丰富结构)"""

    success: bool
    mode: ExtractMode = ExtractMode.LIGHT

    # 事实提取
    facts: List[Dict[str, Any]] = field(default_factory=list)

    # 实体与关系
    entities: List[Dict[str, Any]] = field(default_factory=list)
    relations: List[Dict[str, Any]] = field(default_factory=list)

    # 情绪标注
    emotional_valence: float = 0.0
    emotional_arousal: float = 0.0

    # 时间信息
    temporal_expressions: List[Dict[str, Any]] = field(default_factory=list)

    # 意图检测
    intentions: List[Dict[str, Any]] = field(default_factory=list)

    # 画像信息
    profile_updates: List[Dict[str, Any]] = field(default_factory=list)

    # 更新类型判断 (深度模式)
    update_classifications: List[Dict[str, Any]] = field(default_factory=list)

    tokens_used: int = 0
    error: Optional[str] = None


# ================================================================
# Prompt 模板
# ================================================================

# 统一提取 Prompt
#
# 设计要点（v0.3.45_v0 post8）：
#   - 不再输出 basic_info 字段；稳定结构化属性（name/age/location/occupation/employer）
#     由 LLM 通过 function-calling tool `update_basic_user_profile` 表达。
#   - 不再输出 time_ref 字段。时间相关信息如果是时间敏感的（schedule / past event），
#     让 LLM 把已解析的时间自然嵌入 content 即可；稳定偏好类 content 保持不含时间。

# Prompt 文本统一放在 hy_memory/prompts/<language>.py，按语言码经 get_prompt()
# 取用。以下旧名字保留为英文版的别名（向后兼容）。
from ..prompts.en import EXTRACT_SYSTEM_PROMPT  # noqa: E402,F401  (re-export)
from ..prompts.en import STRUCTURE_METADATA_PROMPT as STRUCTURE_METADATA_PROMPT_EN  # noqa: E402,F401  (re-export)
from ..prompts.en import EXTRACT_FEW_SHOT as EXTRACT_FEW_SHOT_EN  # noqa: E402,F401  (re-export)
from ..prompts.en import EXTRACT_PROMPT  # noqa: E402,F401  (re-export)
from ..prompts.en import EXTRACT_PROMPT_NO_INTENTION  # noqa: E402,F401  (re-export)


# ================================================================
# 核心实现
# ================================================================


def _system_prompt_without_intention(system_prompt: str, *, lang: str) -> str:
    """从 system prompt 去掉 intentions 分桶，改为单一 memory（含未来计划）。

    ``lang`` 是语言码（``en`` / ``zh-Hans`` / ``ja`` ...；旧值 ``zh`` 视作 zh-Hans）。
    每种语言的替换片段在 ``hy_memory/prompts/<lang>.py`` 中定义。
    """
    import re

    language = "zh-Hans" if lang == "zh" else lang
    repl = get_prompt("NO_INTENTION_MEMORY_SECTION", language)
    system_prompt = re.sub(
        get_prompt("NO_INTENTION_SECTION_PATTERN", language),
        lambda _m: repl,
        system_prompt,
        count=1,
        flags=re.S,
    )
    for old, new in get_prompt("NO_INTENTION_REPLACEMENTS", language):
        system_prompt = system_prompt.replace(old, new)
    # few-shot 里若仍出现 intentions，用硬约束盖住
    return system_prompt + get_prompt("INTENTION_DISABLED_NOTE", language)


class Extractor:
    """
    提取智能体 (V2)

    支持 V1 兼容接口 + V2 双模式接口。
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        llm_config: Optional[GlobalLLMConfig] = None,
    ):
        self.llm = llm_provider
        self._llm_config = llm_config or GlobalLLMConfig()
        self._call_count = 0
        self._total_tokens = 0
        logger.info("Extractor V2 initialized")

    # ================================================================
    # V1 兼容接口
    # ================================================================

    async def extract(
        self,
        content: str,
        context: Dict[str, Any] = None,
        extract_types: List[str] = None,
        current_time: str = "",
        existing_tags: Optional[List[str]] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_registry: Optional[Any] = None,
        tool_context: Optional[Dict[str, Any]] = None,
        history_context: str = "",
        basic_profile_fields: Optional[Dict[str, str]] = None,
        language: Optional[str] = None,
    ) -> ExtractResult:
        """
        统一提取：identity / facts / basic_info

        Args:
            content:       原始对话内容
            context:       业务上下文（目前透传，不使用）
            extract_types: 兼容参数（未使用）
            current_time:  当前时间字符串
            existing_tags: 可选，该用户已有的所有 tags（供 LLM 优先复用）
            tools:         **DEPRECATED**: 自 v0.1.5.13 起 extractor 不再调用 LLM
                           function-calling 工具——基础画像通过 prompt + JSON 输出
                           的 basic_info 字段表达。保留参数仅为向后兼容；传入也不
                           会被透传到 LLM API。
            tool_registry: **DEPRECATED**: 同上，不再使用。
            tool_context:  保留，作为 dispatch context 传给（未来可能扩展的）非 basic_info
                           tool；当前未使用。
            basic_profile_fields:
                **DEPRECATED**: 曾用于渲染 EXTRACT prompt 的基础画像 schema 段落，
                现已移除该段落（basic_info 由内置 few-shot / 指令引导）。保留参数
                仅为向后兼容，传入不再生效。
            language:      显式 prompt 语言码（见 hy_memory.prompts.SUPPORTED_LANGUAGES，
                           接受别名）。None 时按 content 做 zh/en 自动检测。显式指定时
                           追加 OUTPUT_LANGUAGE_DIRECTIVE，记忆正文用该语言书写。
        """
        MAX_TOOL_ROUNDS = 5

        try:
            # 构建 last_messages（传入的 history_context 就是格式化好的历史对话文本）
            _last_messages = history_context or "(none)"

            # 构建日期字段
            from datetime import date as _date_cls

            _current_date = (
                _date_cls.today().isoformat()
            )  # 系统当前日期 e.g. "2026-05-23"
            # memory_at: 转为日精度
            if current_time:
                try:
                    from datetime import datetime as _dt_cls

                    _memory_at = _dt_cls.fromisoformat(current_time).strftime(
                        "%Y-%m-%d"
                    )
                except (ValueError, TypeError):
                    _memory_at = _current_date
            else:
                _memory_at = _current_date

            # 选 prompt 语言：显式 language（per-call / MemoryConfig.language）优先；
            # 未指定时沿用 zh/en 自动检测（行为与以前完全一致）。
            _explicit_language = normalize_language(language)
            _language = resolve_language(_explicit_language, content)

            _enable_intention = bool(
                getattr(self._llm_config, "enable_intention", True)
            )
            # client 自定义 system prompt 仅覆盖对应语言（zh → zh-Hans, en → en）；
            # 直接用，不追加内置 few-shot
            _custom_sys = None
            if _language == "zh-Hans":
                _custom_sys = getattr(
                    self._llm_config, "extract_system_prompt_zh", None
                )
            elif _language == "en":
                _custom_sys = getattr(
                    self._llm_config, "extract_system_prompt_en", None
                )
            if _custom_sys:
                system_prompt = _custom_sys
            else:
                system_prompt = get_prompt("EXTRACT_SYSTEM_PROMPT", _language)
                if getattr(self._llm_config, "few_shot_enabled", False):
                    system_prompt = system_prompt + get_prompt(
                        "EXTRACT_FEW_SHOT", _language
                    )
            if not _enable_intention and not _custom_sys:
                system_prompt = _system_prompt_without_intention(
                    system_prompt, lang=_language
                )
            if getattr(self._llm_config, "structure_metadata_enabled", False):
                system_prompt += get_prompt("STRUCTURE_METADATA_PROMPT", _language)
            system_prompt = with_output_language(
                system_prompt, _language, explicit=_explicit_language is not None,
            )
            _user_tmpl = get_prompt(
                "EXTRACT_PROMPT" if _enable_intention else "EXTRACT_PROMPT_NO_INTENTION",
                _language,
            )
            prompt = _user_tmpl.format(
                content=content,
                current_date=_current_date,
                memory_at=_memory_at,
                last_messages=_last_messages,
            )

            # 累计 token 统计
            if _log_llm_content_enabled():
                logger.debug(f"[extractor] user prompt: {prompt!r}")
            else:
                logger.debug(
                    "[extractor] user prompt hidden; len=%d sha256=%s",
                    len(prompt), _content_fingerprint(prompt),
                )
            total_tokens = 0
            total_prompt_tokens = 0
            total_completion_tokens = 0
            llm_finish_reason = ""
            llm_model = ""
            llm_response_id = ""
            llm_choices_count = 0
            llm_content_len = 0
            llm_tool_calls_count = 0
            llm_reasoning_tokens = 0
            llm_reasoning_len = 0
            all_tool_calls: List[Dict[str, Any]] = []
            all_tool_results: List[Dict[str, Any]] = []

            # 构造 messages（system + user）
            messages: List[Dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ]
            # 保存实际 prompt 用于 trace 日志
            _actual_user_prompt = prompt
            _actual_sys_prompt = system_prompt

            # 第一轮调用
            # NOTE: tools / tool_choice 已废弃 — 基础画像走 prompt + JSON basic_info
            # 字段，而不是 LLM function-calling tool（弱模型乱填）。
            # 若调用方传入 tools/tool_registry，会被忽略。
            response = await self.llm.complete_messages(
                messages=messages,
                max_tokens=self._llm_config.agent_max_tokens,
                tools=None,
                tool_choice=None,
                audit_stage="extract",
            )
            self._call_count += 1
            total_tokens += response.tokens_used
            total_prompt_tokens += response.prompt_tokens
            total_completion_tokens += response.completion_tokens

            raw_text = response.content or ""
            llm_finish_reason = getattr(response, "finish_reason", "") or ""
            llm_model = getattr(response, "model", "") or ""
            llm_response_id = getattr(response, "response_id", "") or ""
            llm_choices_count = getattr(response, "choices_count", 0) or 0
            llm_content_len = len(raw_text)
            llm_tool_calls_count = len(response.tool_calls or [])
            llm_reasoning_tokens = getattr(response, "reasoning_tokens", 0) or 0
            llm_reasoning_len = len(getattr(response, "reasoning_content", "") or "")

            # 多轮 tool-use loop（仅当传入 tool_registry 时启用）
            if response.tool_calls and tool_registry:
                from .tools.base import parse_tool_calls_from_json

                round_count = 0
                while response.tool_calls and round_count < MAX_TOOL_ROUNDS:
                    round_count += 1
                    logger.info(
                        f"[extractor] tool-use round {round_count}: "
                        f"{len(response.tool_calls)} tool call(s)"
                    )

                    all_tool_calls.extend(response.tool_calls)

                    # 追加 assistant 消息（含 tool_calls）
                    messages.append(
                        {
                            "role": "assistant",
                            "content": response.content or "",
                            "tool_calls": response.tool_calls,
                        }
                    )

                    # 执行每个 tool call
                    parsed_calls = parse_tool_calls_from_json(response.tool_calls)
                    for tc_raw, tc_parsed in zip(
                        response.tool_calls, parsed_calls or []
                    ):
                        tc_id = tc_raw.get("id") or ""
                        logger.info(
                            f"[extractor] tool call: name={tc_parsed.name}, "
                            f"tool_call_id={tc_id!r}, "
                            f"raw_keys={list(tc_raw.keys())}"
                        )
                        if not tc_id:
                            logger.error(
                                f"[extractor] tool_call_id is empty! "
                                f"raw_keys={list(tc_raw.keys())}"
                            )
                        try:
                            t_res = await tool_registry.dispatch(
                                tc_parsed, tool_context or {}
                            )
                            result_content = json.dumps(
                                {
                                    "success": t_res.success,
                                    "data": t_res.data,
                                    "error": t_res.error,
                                },
                                ensure_ascii=False,
                                default=str,
                            )
                            all_tool_results.append(
                                {
                                    "tool": tc_parsed.name,
                                    "round": round_count,
                                    "success": t_res.success,
                                    "data": t_res.data,
                                    "error": t_res.error,
                                }
                            )
                        except Exception as tool_err:
                            logger.error(
                                f"[extractor] tool '{tc_parsed.name}' raised: {tool_err}",
                                exc_info=True,
                            )
                            result_content = json.dumps(
                                {"success": False, "error": str(tool_err)},
                                ensure_ascii=False,
                            )
                            all_tool_results.append(
                                {
                                    "tool": tc_parsed.name,
                                    "round": round_count,
                                    "success": False,
                                    "error": str(tool_err),
                                }
                            )

                        messages.append(
                            {
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "content": result_content,
                            }
                        )

                    # 下一轮 LLM 调用（已废弃 tools；保留 dispatch 路径仅用于
                    # 极端情况下 LLM 通过其他途径返回 tool_calls 的兼容处理）
                    response = await self.llm.complete_messages(
                        messages=messages,
                        max_tokens=self._llm_config.agent_max_tokens,
                        tools=None,
                        tool_choice=None,
                        audit_stage="extract",
                    )
                    self._call_count += 1
                    total_tokens += response.tokens_used
                    total_prompt_tokens += response.prompt_tokens
                    total_completion_tokens += response.completion_tokens
                    raw_text = response.content or ""
                    llm_finish_reason = getattr(response, "finish_reason", "") or ""
                    llm_model = getattr(response, "model", "") or ""
                    llm_response_id = getattr(response, "response_id", "") or ""
                    llm_choices_count = getattr(response, "choices_count", 0) or 0
                    llm_content_len = len(raw_text)
                    llm_tool_calls_count = len(response.tool_calls or [])
                    llm_reasoning_tokens = getattr(response, "reasoning_tokens", 0) or 0
                    llm_reasoning_len = len(getattr(response, "reasoning_content", "") or "")

                if round_count >= MAX_TOOL_ROUNDS and response.tool_calls:
                    logger.warning(
                        f"[extractor] tool-use loop hit max rounds ({MAX_TOOL_ROUNDS})"
                    )

                logger.info(
                    f"[extractor] tool-use loop done: {round_count} round(s), "
                    f"{len(all_tool_calls)} tool call(s), content_len={len(raw_text)}"
                )

            elif response.tool_calls and not tool_registry:
                # 没有 tool_registry 时，透传 tool_calls 给上层（向后兼容）
                all_tool_calls = response.tool_calls

            self._total_tokens += total_tokens

            # 空响应判断
            if (not raw_text or not raw_text.strip()) and not all_tool_calls:
                logger.warning(
                    "[extractor] LLM returned empty response "
                    f"finish_reason={llm_finish_reason!r} model={llm_model!r} "
                    f"response_id={llm_response_id!r} choices={llm_choices_count} "
                    f"content_len={llm_content_len} prompt_tokens={total_prompt_tokens} "
                    f"completion_tokens={total_completion_tokens} total_tokens={total_tokens} "
                    f"reasoning_tokens={llm_reasoning_tokens} reasoning_len={llm_reasoning_len}"
                    + (
                        " | HINT: reasoning exhausted the output budget "
                        "(completion_tokens == max_tokens). Disable thinking "
                        "(hy3: reasoning_effort=no_think) or raise MEMORY_AGENT_MAX_TOKENS."
                        if llm_reasoning_tokens > 0
                        and llm_finish_reason == "length"
                        else ""
                    )
                )
                return ExtractResult(
                    success=False,
                    error="LLM returned empty response",
                    error_code="EMPTY_RESPONSE",
                    raw_response=raw_text,
                    tokens_used=total_tokens,
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    llm_finish_reason=llm_finish_reason,
                    llm_model=llm_model,
                    llm_response_id=llm_response_id,
                    llm_choices_count=llm_choices_count,
                    llm_content_len=llm_content_len,
                    llm_tool_calls_count=llm_tool_calls_count,
                    llm_reasoning_tokens=llm_reasoning_tokens,
                    llm_reasoning_len=llm_reasoning_len,
                    _actual_prompt=_actual_user_prompt,
                    _actual_system_prompt=_actual_sys_prompt,
                )

            # 解析 JSON
            info: Dict[str, Any]
            if raw_text and raw_text.strip():
                parsed = self._parse_json(raw_text)
                if parsed is None:
                    if not all_tool_calls:
                        logger.warning(
                            f"[extractor] JSON parse failed ({len(raw_text)} chars)"
                        )
                        logger.debug(f"[extractor] unparsable output: {raw_text[:200]!r}")
                        return ExtractResult(
                            success=False,
                            error=f"JSON parse failed ({len(raw_text)} chars)",
                            error_code="JSON_PARSE_FAILED",
                            raw_response=raw_text,
                            tokens_used=total_tokens,
                            prompt_tokens=total_prompt_tokens,
                            completion_tokens=total_completion_tokens,
                            llm_finish_reason=llm_finish_reason,
                            llm_model=llm_model,
                            llm_response_id=llm_response_id,
                            llm_choices_count=llm_choices_count,
                            llm_content_len=llm_content_len,
                            llm_tool_calls_count=llm_tool_calls_count,
                            _actual_prompt=_actual_user_prompt,
                            _actual_system_prompt=_actual_sys_prompt,
                        )
                    info = {}
                else:
                    info = parsed
            else:
                info = {}

            # 记录 tool 调用信息供上层（pipeline log / writer）使用
            if all_tool_calls:
                info["tool_calls"] = all_tool_calls
            if all_tool_results:
                info["tool_results"] = all_tool_results

            # One extraction call covers one current write and cannot establish
            # repetition across independent occurrences. Keep any bounded
            # Episode, but defer Series creation to reconcile where historical
            # candidate writes are visible.
            if getattr(self._llm_config, "structure_metadata_enabled", False):
                from ..structure import strip_series_from_assignments

                for _key in ("memory", "facts", "identity"):
                    _items = info.get(_key)
                    if not isinstance(_items, list):
                        continue
                    for _item in _items:
                        if isinstance(_item, dict) and "topic_assignments" in _item:
                            _item["topic_assignments"] = strip_series_from_assignments(
                                _item.get("topic_assignments")
                            )

            # Intention 开关关闭：即使模型仍返回 intentions，也丢弃（写侧另有硬闸）
            if not _enable_intention:
                if info.get("intentions"):
                    logger.info(
                        "[extractor] enable_intention=false, dropping %d intention(s)",
                        len(info.get("intentions") or []),
                    )
                info["intentions"] = []

            # basic_info is the canonical home for the user's name. Models may
            # still echo the same fact into L2 despite the prompt; remove only an
            # exact name-only statement, never a sentence carrying other facts.
            _basic = info.get("basic_info") if isinstance(info, dict) else None
            _name = str((_basic or {}).get("name") or "").strip()
            if _name:
                _name_key = re.escape(_name)
                _name_only = re.compile(
                    rf"^(?:the\s+user(?:'s|’s)\s+name\s+is|"
                    rf"the\s+user\s+is\s+(?:named|called))\s+{_name_key}[.!。]?$|"
                    rf"^(?:用户(?:的名字|名)?(?:是|叫))\s*{_name_key}[.!。]?$",
                    flags=re.I,
                )
                for _key in ("memory", "facts"):
                    _items = info.get(_key)
                    if isinstance(_items, list):
                        info[_key] = [
                            item for item in _items
                            if not (
                                isinstance(item, dict)
                                and _name_only.fullmatch(
                                    str(item.get("content") or "").strip()
                                )
                            )
                        ]

            # A strong model can still repeat one durable proposition twice
            # inside the same aggregated L2. Remove only near-contained complete
            # sentences; keep short fragments, dates and causal/detail clauses.
            for _key in ("memory", "facts"):
                _items = info.get(_key)
                if not isinstance(_items, list):
                    continue
                for _item in _items:
                    if not isinstance(_item, dict):
                        continue
                    _content = str(_item.get("content") or "").strip()
                    if _content:
                        _item["content"] = dedup_internal_memory_sentences(_content)

            tool_calls_only = (
                bool(all_tool_calls)
                and not info.get("memory")
                and not info.get("facts")  # legacy compat
                and not info.get("intentions")
                and not info.get("identity")  # legacy compat
            )

            result = ExtractResult(
                success=True,
                info=info,
                confidence=0.8,
                raw_response=raw_text,
                tokens_used=total_tokens,
                prompt_tokens=total_prompt_tokens,
                completion_tokens=total_completion_tokens,
                llm_finish_reason=llm_finish_reason,
                llm_model=llm_model,
                llm_response_id=llm_response_id,
                llm_choices_count=llm_choices_count,
                llm_content_len=llm_content_len,
                llm_tool_calls_count=llm_tool_calls_count,
                tool_calls_only=tool_calls_only,
                _actual_prompt=_actual_user_prompt,
                _actual_system_prompt=_actual_sys_prompt,
            )
            return result
        except Exception as e:
            # 带异常类型 + 完整 traceback，避免只剩笼统 "LLM_ERROR" 无从排查。
            # 若为 LLM 重试耗尽，RuntimeError 的 __cause__ 里含真实根因（超时/401/连接等），
            # 且 LLMRetryError.attempts 带每一次 attempt 的失败明细。
            logger.error(f"Extractor.extract failed: {type(e).__name__}: {e}", exc_info=True)
            _root = e.__cause__ or e
            _err_msg = f"{type(e).__name__}: {e}"
            if _root is not e:
                _err_msg += f" | root cause: {type(_root).__name__}: {_root}"
            _attempts = list(getattr(e, "attempts", []) or [])
            return ExtractResult(
                success=False,
                error=_err_msg,
                error_code="LLM_ERROR",
                attempts=_attempts,
                _actual_prompt=(
                    _actual_user_prompt if "_actual_user_prompt" in dir() else None
                ),
                _actual_system_prompt=(
                    _actual_sys_prompt if "_actual_sys_prompt" in dir() else None
                ),
            )

    async def extract_profile(
        self,
        content: str,
        context: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        """专门提取用户画像 (profile 已包含 preferences)"""
        result = await self.extract(content, context)
        if result.success:
            return result.info.get("profile", {})
        return {}

    async def extract_entities(
        self,
        content: str,
        context: Dict[str, Any] = None,
    ) -> List[Dict[str, Any]]:
        """提取实体"""
        result = await self.extract(content, context)
        if result.success:
            return result.info.get("entities", [])
        return []

    # ================================================================
    # 工具方法
    # ================================================================

    def _parse_json(self, text: str) -> Optional[Dict[str, Any]]:
        """从 LLM 输出中解析 JSON。成功返回 dict，解析失败返回 None。"""
        text = text.strip()
        # 处理 markdown 代码块
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 尝试找到 JSON 块
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        # 最后兜底：json_repair 修复常见 LLM 残缺输出（缺逗号/引号/未闭合等）
        try:
            from json_repair import repair_json

            repaired = repair_json(text, return_objects=True)
            if isinstance(repaired, dict) and repaired:
                logger.info("[extractor] JSON recovered via json_repair")
                return repaired
        except Exception as _re:
            logger.debug(f"[extractor] json_repair failed: {_re}")
        return None

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            "call_count": self._call_count,
            "total_tokens": self._total_tokens,
            "avg_tokens_per_call": (
                self._total_tokens / self._call_count if self._call_count > 0 else 0
            ),
        }
