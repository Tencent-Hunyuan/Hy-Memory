# -*- coding: utf-8 -*-
"""
Coding Memory - Scene Judge

两个职责（同模块、共用 LLMConfig.model）：

1. classify_messages_scene(messages, llm)
   写入端：基于 user query + tool 名称的简化视图，对整段 messages 做
   chat / working / coding 三分类。不切 segment、不分 turn。

2. classify_queries_scene(queries, llm)
   搜索端：以 queries 中末尾为目标 query，前置 queries 作为上下文，判定具体 scene。

旧的 classify_*_is_coding API 保留兼容：working/coding 都返回 True。

对外 scene 与自动路由语义见 docs/scene_coding_usage.md。
"""

import json
import logging
import time
from typing import List, Optional, Dict, Any

from ..agent.llm_provider import LLMProvider
from ..pipelines.base import ChatMessage
from .preproc import extract_tool_summary

logger = logging.getLogger(__name__)

_JUDGE_MAX_TURNS = 20
_JUDGE_HEAD_TURNS = 5
_JUDGE_USER_CONTENT_LIMIT = 1_000
_JUDGE_USER_CONTENT_HEAD = 200
_JUDGE_USER_CONTENT_TAIL = 200
_JUDGE_MAX_QUERY_CHARS = 300
_JUDGE_MAX_QUERIES = 20


async def _store_coding_log(
    cache: Any,
    *,
    request_id: str,
    user_id: str,
    agent_id: str,
    session_id: str,
    step: str,
    prompt: str,
    response: str,
    parsed: str = "",
    elapsed_ms: float = 0.0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    total_tokens: int = 0,
) -> None:
    """coding 链路统一打点 helper：best-effort，失败只 warning，绝不影响主流程。

    cache 为 None（未注入）时直接跳过，coding 模块可独立使用/单测。
    """
    if cache is None:
        return
    try:
        await cache.store_pipeline_log(
            request_id=request_id or "",
            user_id=user_id or "",
            agent_id=agent_id or "default_agent",
            session_id=session_id or "",
            step=step,
            prompt=prompt or "",
            response=response or "",
            parsed=parsed or "",
            elapsed_ms=elapsed_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
        )
    except Exception as e:
        logger.warning(f"[coding-log] store {step} failed: {e}")


def _resolve_llm_temperature(
    llm_provider: LLMProvider, default: Optional[float] = None,
) -> Optional[float]:
    """解析温度；返回 None 表示**不传该参数**，由 provider 统一决定。

    此前这里 fallback 到 0.1，会绕过用户配置（kimi 等平台对 temperature 有硬约束，
    Opus 5 更是只接受 1 或不传）。现在配置要求省略时返回 None，一路传到 provider
    就不会出现在请求里。
    """
    config = getattr(llm_provider, "config", None)
    if config is not None:
        llm = getattr(config, "llm", None)
        if llm is not None:
            if getattr(llm, "omit_temperature", False):
                return None
            temp = getattr(llm, "temperature", None)
            if temp is not None:
                return float(temp)
    inner = getattr(llm_provider, "_llm_config", None)
    temp = getattr(inner, "temperature", None) if inner is not None else None
    if temp is not None:
        return float(temp)
    return default


# ================================================================
# 写入端：整段三分类
# ================================================================

CLASSIFY_MESSAGES_PROMPT = """\
You are a single-label scene classifier. Decide the DOMINANT scene of the entire
conversation chunk passed to you.

Output exactly one of:
  "coding"  ← source code or another developer artifact is the work object or the
              implementation medium. Examples: writing/reading/editing/debugging/
              refactoring code; scripts, SQL, notebooks or automation code; tests,
              builds, compiler/runtime errors, APIs, SDKs, dependencies, code review,
              deployment code/config tightly coupled to software development. If
              code is explicitly used to build the solution, choose coding even if
              the business goal itself sounds like an office/working task.
  "working" ← a concrete productivity task is being carried out, but code is not
              the central work object/medium. Examples: documents, reports, PPT/
              slides, spreadsheets, office workflows, writing/editing/translation,
              finance/accounting/investment/business analysis, research and web
              research for a deliverable, email/calendar/project management, data
              entry and non-code data analysis, image/design/content production,
              file/folder organization, software installation/configuration,
              device/disk/backup/system operations, and other computer operations.
  "chat"    ← casual conversation, personal information/preferences, advice or
              general factual Q&A with no productivity deliverable or operation.
              Search/browse tools do NOT make a task working: checking weather,
              news, celebrities, restaurants or similar facts for conversation is
              still chat unless the results are used to produce a concrete work
              artifact or execute a productivity task.

Rules:
- Look at the WHOLE chunk, not individual turns. A few stray off-topic turns
  inside a clearly task-focused chunk should not change its scene.
- Tool names are evidence, never the answer by themselves. Infer what the user is
  accomplishing and what artifact is being produced.
- coding beats working when real code is authored, edited, debugged, tested or
  used as the implementation medium. Merely using a browser/search tool is not coding.
- working beats chat only when there is a concrete productivity action or deliverable.
- If evidence is insufficient, choose chat rather than inventing a work scene.

Conversation summary (turns with user query + tool names used):
{turns_block}

Output strict JSON only, no markdown. Keep "reason" very short (max 15 words):
{{"scene": "chat|working|coding", "reason": "..."}}
"""


async def classify_messages_scene(
    messages: List[ChatMessage],
    llm_provider: LLMProvider,
    *,
    max_tokens: int = 400,
    temperature: Optional[float] = None,
    cache: Any = None,
    request_id: str = "",
    user_id: str = "",
    agent_id: str = "default_agent",
    session_id: str = "",
) -> str:
    """
    对整段 messages 做 chat / working / coding 单一场景判定。

    复用 LLMConfig.model（不引入独立 classifier 配置）。
    temperature=None 时使用 config.llm.temperature（与 MemAgent 路径一致），
    避免硬编码 0.0 触发 kimi/deepseek 等模型的温度硬约束。
    fail-safe：LLM 调用失败或解析失败，返回 "chat"。

    cache 非空时落 CODING_JUDGE pipeline log（无论判 coding 还是 chat 都记录一条）。
    """
    if not messages:
        return "chat"

    summary = extract_tool_summary(messages)
    turns_block = _format_turns(summary)
    prompt = CLASSIFY_MESSAGES_PROMPT.format(turns_block=turns_block)

    if temperature is None:
        temperature = _resolve_llm_temperature(llm_provider)

    _t0 = time.perf_counter()
    try:
        resp = await llm_provider.complete(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            audit_stage="coding_judge",
        )
        _elapsed = round((time.perf_counter() - _t0) * 1000, 2)
        result = _parse_scene_json(resp.content)
        if result is None:
            logger.warning(
                f"[coding-judge] failed to parse classify_messages output: {resp.content!r}; defaulting to chat"
            )
            await _store_coding_log(
                cache, request_id=request_id, user_id=user_id, agent_id=agent_id,
                session_id=session_id, step="CODING_JUDGE", prompt=prompt,
                response=resp.content or "", parsed=json.dumps({"scene": "chat", "is_coding": False, "parse_error": True}, ensure_ascii=False),
                elapsed_ms=_elapsed, prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens, total_tokens=resp.tokens_used,
            )
            return "chat"
        scene = result["scene"]
        parsed = {**result, "is_coding": scene != "chat"}
        logger.info(
            f"[coding-judge] scene={scene} reason={result.get('reason', '')!r}"
        )
        await _store_coding_log(
            cache, request_id=request_id, user_id=user_id, agent_id=agent_id,
            session_id=session_id, step="CODING_JUDGE", prompt=prompt,
            response=resp.content or "", parsed=json.dumps(parsed, ensure_ascii=False),
            elapsed_ms=_elapsed, prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens, total_tokens=resp.tokens_used,
        )
        return scene
    except Exception as e:
        logger.warning(f"[coding-judge] classify_messages LLM failed: {e}; defaulting to chat")
        await _store_coding_log(
            cache, request_id=request_id, user_id=user_id, agent_id=agent_id,
            session_id=session_id, step="CODING_JUDGE", prompt=prompt,
            response=f"[LLM error] {type(e).__name__}: {str(e)[:300]}",
            parsed=json.dumps({"scene": "chat", "is_coding": False, "llm_error": True}, ensure_ascii=False),
            elapsed_ms=round((time.perf_counter() - _t0) * 1000, 2),
        )
        return "chat"


async def classify_messages_is_coding(
    messages: List[ChatMessage],
    llm_provider: LLMProvider,
    **kwargs: Any,
) -> bool:
    """兼容旧 API：working/coding 都表示应走生产力记忆链路。"""
    return (await classify_messages_scene(messages, llm_provider, **kwargs)) != "chat"


# ================================================================
# 搜索端：用 queries 上下文判类
# ================================================================

CLASSIFY_QUERIES_PROMPT = """\
Classify the LATEST query as "chat", "working", or "coding".
Use the earlier queries (if any) as context for disambiguation.

Use "coding" when source code, scripts, SQL/notebooks, tests/builds, APIs/SDKs,
dependencies, debugging or code review are the work object or implementation
medium. If code is explicitly used to produce the result, choose coding.

Use "working" for concrete non-code productivity work: documents/reports/PPT,
spreadsheets, writing/translation, finance or business analysis, research for a
deliverable, email/calendar/project management, content/design, file organization,
software/device configuration, disk/backup/system operations, and similar work.

Use "chat" for casual conversation, personal information/preferences, advice or
general factual Q&A with no productivity deliverable. Search/browse tools alone do
not make work: weather/news/celebrity/restaurant lookup for conversation is chat.
Tool names are evidence, never the classification by themselves.

Recent queries (oldest → newest, last is target):
{queries_block}

Output strict JSON only, no markdown:
{{"scene": "chat|working|coding"}}
"""


async def classify_queries_scene(
    queries: List[str],
    llm_provider: LLMProvider,
    *,
    max_tokens: int = 100,
    temperature: Optional[float] = None,
) -> str:
    """对 queries 判定 chat / working / coding；失败时回退 chat。"""
    if not queries:
        return "chat"

    bounded_queries = _bounded_queries(queries)
    queries_block = "\n".join(
        f"{i + 1}. {repr(q)}" + ("   ← target" if i == len(bounded_queries) - 1 else "")
        for i, q in enumerate(bounded_queries)
    )
    prompt = CLASSIFY_QUERIES_PROMPT.format(queries_block=queries_block)
    if temperature is None:
        temperature = _resolve_llm_temperature(llm_provider)
    try:
        resp = await llm_provider.complete(
            prompt=prompt, max_tokens=max_tokens, temperature=temperature,
        )
        result = _parse_scene_json(resp.content)
        if result is None:
            logger.warning(
                f"[coding-judge] failed to parse classify_queries output: {resp.content!r}; defaulting to chat"
            )
            return "chat"
        scene = result["scene"]
        logger.info(f"[coding-judge] queries scene={scene}")
        return scene
    except Exception as e:
        logger.warning(f"[coding-judge] classify_queries LLM failed: {e}; defaulting to chat")
        return "chat"


async def classify_queries_is_coding(
    queries: List[str],
    llm_provider: LLMProvider,
    *,
    max_tokens: int = 100,
    temperature: Optional[float] = None,
) -> bool:
    """
    对一组 queries（末尾为目标）判类。

    单 query 输入也支持（前置上下文为空）。
    temperature=None 时使用 config.llm.temperature。
    fail-safe：LLM 调用失败或解析失败，返回 False（走现有 chat 召回）。
    """
    return (await classify_queries_scene(
        queries, llm_provider, max_tokens=max_tokens, temperature=temperature,
    )) != "chat"


# ================================================================
# 搜索端：极简判类 + 改写（单次 LLM，搜索路径必须低时延）
# ================================================================

CLASSIFY_AND_REWRITE_PROMPT = """\
Task: Given queries (oldest -> newest, last is target), do TWO things:
1) Classify the target as chat, working, or coding using the same boundary:
   coding = code/scripts/SQL/notebooks/tests/builds/APIs/debugging are the work;
   working = concrete non-code productivity deliverable or computer operation;
   chat = casual/personal/general Q&A, including incidental web-search lookups.
   Tool use alone never decides the scene.
2) Rewrite the target into ONE concise standalone query, expanding pronouns /
   restoring omitted context using the earlier queries. Keep the same language.

Output STRICTLY in this format, nothing else:
<chat, working, or coding>
<rewritten query>

Queries:
{queries_block}
"""


async def classify_and_rewrite_queries(
    queries: List[str],
    llm_provider: LLMProvider,
    *,
    max_tokens: int = 200,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    """
    单次 LLM 调用同时做：
    1) 判定 target query (queries[-1]) 是 chat / working / coding
    2) 用前置 queries 上下文做改写（消除指代 / 补全省略），返回 rewrite_query

    输出格式刻意保持极简（避免 JSON 解析开销 + 让 LLM 输出短）：
        第一行: chat / working / coding（兼容旧 0/1）
        第二行: rewritten query

    Returns:
        {"scene": str, "is_coding": bool, "rewrite_query": str, "ok": bool}
        失败时 scene=chat、ok=False，rewrite_query 等于 queries[-1]

    fail-safe：LLM 失败 / 解析失败一律 ok=False（caller 应回退默认 chat 路径）。
    """
    target = queries[-1] if queries else ""
    fallback = {"scene": "chat", "is_coding": False, "rewrite_query": target, "ok": False}
    if not queries:
        return fallback

    bounded_queries = _bounded_queries(queries)
    queries_block = "\n".join(
        f"- {q}" + ("   <-- target" if i == len(bounded_queries) - 1 else "")
        for i, q in enumerate(bounded_queries)
    )
    prompt = CLASSIFY_AND_REWRITE_PROMPT.format(queries_block=queries_block)

    if temperature is None:
        temperature = _resolve_llm_temperature(llm_provider)

    try:
        resp = await llm_provider.complete(
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )
    except Exception as e:
        logger.warning(f"[coding-judge] classify_and_rewrite LLM failed: {e}; falling back to chat")
        return fallback

    text = (resp.content or "").strip()
    if not text:
        logger.warning("[coding-judge] classify_and_rewrite empty content; falling back to chat")
        return fallback

    # 去 markdown fence（保险）
    if text.startswith("```"):
        lines = text.split("\n")
        body = []
        in_fence = False
        for line in lines:
            if line.startswith("```"):
                in_fence = not in_fence
                continue
            body.append(line)
        text = "\n".join(body).strip() or text

    # 拆解：第一行 = scene（兼容旧 0/1），剩下的 = rewrite
    lines = text.split("\n", 1)
    head = lines[0].strip().rstrip(",.").rstrip("。，")
    rewrite = (lines[1].strip() if len(lines) > 1 else "").strip()
    if not rewrite:
        rewrite = target  # 没改写就用原 target

    scene = _parse_scene_label(head)
    if scene is None:
        logger.warning(
            f"[coding-judge] classify_and_rewrite unrecognized head={head!r}; falling back to chat"
        )
        return {"scene": "chat", "is_coding": False, "rewrite_query": rewrite, "ok": False}
    is_coding = scene != "chat"

    logger.info(
        f"[coding-judge] classify_and_rewrite scene={scene} "
        f"rewrite={rewrite!r}"
    )
    return {"scene": scene, "is_coding": is_coding, "rewrite_query": rewrite, "ok": True}


# ================================================================
# 搜索端：合并分析（判类 + 改写 + 时间跨度），单次 LLM
#
# 支持三种组合（由 caller 通过 want_classify / want_rewrite 控制）：
#   - want_classify + want_rewrite : working memory + query rewrite 同开，一次 LLM
#   - want_rewrite only            : 只 query rewrite（恒 chat / 不判类）
#   - want_classify only 走旧的 classify_and_rewrite_queries / classify_queries_is_coding
#
# 时间：LLM 只输出相对锚点（today / N days ago / an ISO date），由本函数结合
# current_time 换算成 created_after（unix 秒，下界）。无上界。
# ================================================================

ANALYZE_QUERIES_PROMPT = """\
You rewrite a user's memory-search query. Given queries (oldest -> newest, last is the target){classify_clause}.

Current time: {current_time}

Do:
{task_lines}
R) Rewrite the target into ONE short, clear, standalone query. Resolve pronouns and
   restore omitted context from earlier queries. Keep the same language. No time words
   in the rewrite (time goes in SINCE).
S) SINCE: if the target refers to a time range (e.g. "yesterday", "last week",
   "刚才", "上个月"), output the START date as YYYY-MM-DD. If none, output NONE.

Output STRICTLY these lines, nothing else:
{output_lines}

Queries:
{queries_block}
"""


async def analyze_queries(
    queries: List[str],
    llm_provider: LLMProvider,
    *,
    want_classify: bool,
    want_rewrite: bool,
    current_time: str = "",
    max_tokens: int = 220,
    temperature: Optional[float] = None,
) -> Dict[str, Any]:
    """
    单次 LLM：按需 (判类) + 改写 + 时间跨度。

    Args:
        want_classify: 是否要 is_coding（working memory 开启时）
        want_rewrite:  是否要 rewrite + timespan（query rewrite 开启时）
        current_time:  系统当前时间（ISO），用于把相对时间换算成 created_after

    Returns:
        {
          "scene": str,               # chat / working / coding
          "is_coding": bool,          # 兼容字段：scene != chat
          "rewrite_query": str,       # 失败/未改写时 = queries[-1]
          "created_after": float|None,# SINCE 换算的 unix 秒下界，无则 None
          "since_raw": str,           # LLM 原始 SINCE（调试用）
          "ok": bool,
        }
    fail-safe：LLM / 解析失败 → ok=False，is_coding=False，rewrite=target，created_after=None
    """
    target = queries[-1] if queries else ""
    fallback = {
        "scene": "chat", "is_coding": False, "rewrite_query": target,
        "created_after": None, "since_raw": "", "ok": False,
    }
    if not queries or not want_rewrite:
        # 不需要 rewrite 就不该调这个函数；防御性返回 fallback
        return fallback

    bounded_queries = _bounded_queries(queries)
    queries_block = "\n".join(
        f"- {q}" + ("   <-- target" if i == len(bounded_queries) - 1 else "")
        for i, q in enumerate(bounded_queries)
    )

    if want_classify:
        classify_clause = ""
        task_lines = (
            "C) Classify the target as chat, working, or coding.\n"
            "   coding: code/scripts/SQL/notebooks/tests/builds/APIs/debugging are\n"
            "   the work object or implementation medium.\n"
            "   working: concrete non-code productivity work such as documents,\n"
            "   PPT/spreadsheets, finance/business analysis, research deliverables,\n"
            "   email/calendar/project work, content/design, file/device/system ops.\n"
            "   chat: casual/personal/general Q&A, including incidental weather/news/\n"
            "   restaurant/celebrity web searches with no productivity deliverable.\n"
            "   Tool use alone never decides the scene.\n"
        )
        output_lines = "<chat, working, or coding>\n<rewritten query>\nSINCE: <YYYY-MM-DD or NONE>"
    else:
        classify_clause = ""
        task_lines = ""
        output_lines = "<rewritten query>\nSINCE: <YYYY-MM-DD or NONE>"

    prompt = ANALYZE_QUERIES_PROMPT.format(
        classify_clause=classify_clause,
        current_time=current_time or "(unknown)",
        task_lines=task_lines,
        output_lines=output_lines,
        queries_block=queries_block,
    )

    if temperature is None:
        temperature = _resolve_llm_temperature(llm_provider)

    try:
        resp = await llm_provider.complete(
            prompt=prompt, max_tokens=max_tokens, temperature=temperature,
        )
    except Exception as e:
        logger.warning(f"[coding-judge] analyze_queries LLM failed: {e}; fallback")
        return fallback

    text = (resp.content or "").strip()
    if not text:
        return fallback

    # 去 markdown fence
    if text.startswith("```"):
        body = [ln for ln in text.split("\n") if not ln.startswith("```")]
        text = "\n".join(body).strip() or text

    lines = [ln for ln in text.split("\n") if ln.strip()]
    if not lines:
        return fallback

    scene = "chat"
    idx = 0
    if want_classify:
        head = lines[0].strip().rstrip(",.").rstrip("。，")
        parsed_scene = _parse_scene_label(head)
        if parsed_scene is None:
            logger.warning(
                f"[coding-judge] analyze_queries unrecognized head={head!r}; ok=False"
            )
            return {**fallback, "since_raw": ""}
        scene = parsed_scene
        idx = 1

    # 找 SINCE 行；其余行拼成 rewrite
    since_raw = ""
    rewrite_lines: List[str] = []
    for ln in lines[idx:]:
        s = ln.strip()
        if s.upper().startswith("SINCE:"):
            since_raw = s[len("SINCE:"):].strip()
        else:
            rewrite_lines.append(s)
    rewrite = " ".join(rewrite_lines).strip() or target

    created_after = _since_to_created_after(since_raw, current_time)

    logger.info(
        f"[coding-judge] analyze scene={scene} rewrite={rewrite!r} "
        f"since={since_raw!r} created_after={created_after}"
    )
    return {
        "scene": scene, "is_coding": scene != "chat", "rewrite_query": rewrite,
        "created_after": created_after, "since_raw": since_raw, "ok": True,
    }


def _since_to_created_after(since_raw: str, current_time: str) -> Optional[float]:
    """把 LLM 输出的 SINCE（YYYY-MM-DD 或 NONE）换算成 unix 秒下界。"""
    if not since_raw:
        return None
    s = since_raw.strip().strip('"').strip("'").rstrip(".,。，")
    if not s or s.upper() in ("NONE", "N/A", "NULL", "-"):
        return None
    from datetime import datetime
    # 只取前 10 个字符（YYYY-MM-DD），容忍 LLM 带时间
    candidate = s[:10]
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            dt = datetime.strptime(candidate, fmt)
            return dt.timestamp()
        except (ValueError, TypeError):
            continue
    logger.warning(f"[coding-judge] analyze_queries unparsable SINCE={since_raw!r}")
    return None


# ================================================================
# Helpers
# ================================================================

def _format_turns(summary: List[Dict[str, Any]]) -> str:
    """渲染 turn 简化视图给 LLM 看。"""
    if not summary:
        return "(empty)"
    if len(summary) > _JUDGE_MAX_TURNS:
        summary = (
            summary[:_JUDGE_HEAD_TURNS]
            + [{"turn": "...", "user": "[middle turns omitted]", "tools": []}]
            + summary[-(_JUDGE_MAX_TURNS - _JUDGE_HEAD_TURNS - 1):]
        )
    rows = []
    for t in summary:
        user = t.get("user", "") or ""
        if len(user) > _JUDGE_USER_CONTENT_LIMIT:
            omitted = len(user) - _JUDGE_USER_CONTENT_HEAD - _JUDGE_USER_CONTENT_TAIL
            user = (
                user[:_JUDGE_USER_CONTENT_HEAD]
                + f"\n[...{omitted} chars omitted...]\n"
                + user[-_JUDGE_USER_CONTENT_TAIL:]
            )
        tools = t.get("tools", []) or []
        rows.append(
            f"- turn {t.get('turn', '?')}: user={user!r}, tools={tools}"
            + (", has_tool_result=True" if t.get("has_tool_result") else "")
        )
    return "\n".join(rows)


def _bounded_queries(queries: List[str]) -> List[str]:
    """给快速 search judge 的有界 query 视图；始终保留 target query。"""
    bounded = [str(q)[:_JUDGE_MAX_QUERY_CHARS] for q in queries]
    if len(bounded) <= _JUDGE_MAX_QUERIES:
        return bounded
    head = bounded[:_JUDGE_HEAD_TURNS]
    tail = bounded[-(_JUDGE_MAX_QUERIES - _JUDGE_HEAD_TURNS - 1):]
    return head + ["[middle queries omitted]"] + tail


def _parse_scene_label(value: Any) -> Optional[str]:
    """解析三分类标签，并兼容旧二分类的 0/1 与 true/false 输出。"""
    raw = str(value or "").strip().strip('"').strip("'").lower()
    raw = raw.rstrip(",.，。")
    if raw in ("chat", "working", "coding"):
        return raw
    if raw in ("1", "true", "yes", "y") or raw.startswith("1 "):
        # 旧协议只有 productivity/chat；productivity 对应历史默认 working variant。
        return "working"
    if raw in ("0", "false", "no", "n") or raw.startswith("0 "):
        return "chat"
    return None


def _parse_scene_json(text: str) -> Optional[Dict[str, Any]]:
    """宽松解析 {scene: ...}，并兼容旧 {is_coding: bool}。"""
    if not text:
        return None
    text = text.strip()
    if text.startswith("```"):
        text = "\n".join(
            line for line in text.split("\n") if not line.startswith("```")
        ).strip()
    obj = None
    try:
        obj = json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(text[start:end + 1])
            except Exception:
                obj = None
    if not isinstance(obj, dict):
        # query 快速路径可能只回一个裸标签。
        scene = _parse_scene_label(text.splitlines()[0] if text else "")
        return {"scene": scene} if scene else None
    scene = _parse_scene_label(obj.get("scene"))
    if scene is None and "is_coding" in obj:
        scene = "working" if bool(obj.get("is_coding")) else "chat"
    if scene is None:
        return None
    return {**obj, "scene": scene}


def _parse_is_coding_json(text: str) -> Optional[Dict[str, Any]]:
    """
    宽松解析 LLM 输出。先试纯 JSON，再试在文本里找第一个 {...} 块。
    返回 None 表示无法解析。
    """
    if not text:
        return None
    raw = text.strip()
    if raw.startswith("```"):
        raw = "\n".join(
            line for line in raw.split("\n") if not line.startswith("```")
        ).strip()
    obj = None
    try:
        obj = json.loads(raw)
    except Exception:
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            try:
                obj = json.loads(raw[start:end + 1])
            except Exception:
                obj = None
    if isinstance(obj, dict) and "is_coding" in obj:
        # 保持旧 helper 的精确返回形状；新三分类逻辑走 _parse_scene_json。
        return obj
    parsed = _parse_scene_json(text)
    if parsed is None:
        return None
    return {"is_coding": parsed["scene"] != "chat"}
