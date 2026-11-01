# -*- coding: utf-8 -*-
"""
Coding Memory 预处理工具（规则，零 LLM）

- has_any_tool_message  : 检测 messages 是否含 tool 消息（O(1) 决定是否进 coding 链）
- strip_tool_messages   : 把 tool 消息 + assistant tool_calls 字段 strip 掉，留给 chat 链
- truncate_tool_message : 长 tool_result 头尾保留中段截断（不做 schema 化压缩）
- extract_files         : 从 tool_calls.arguments 抽 path / file_path 等常见键
- extract_tool_summary  : 写 LLM judge 时给的简化视图

使用方式与 scene 路由见 docs/scene_coding_usage.md。
"""

import re
from typing import Any, Dict, List, Optional, Tuple

from ..pipelines.base import ChatMessage


# ================================================================
# 路径键名（跨 OpenAI / Anthropic / Claude Code 通用）
# ================================================================

PATH_KEYS: Tuple[str, ...] = (
    "path", "file_path", "filename", "file", "filepath",
    "target_path", "notebook_path", "src_path", "dst_path",
)


# ================================================================
# 检测 / 过滤
# ================================================================

def has_any_tool_message(messages: List[ChatMessage]) -> bool:
    """messages 中是否存在 tool 消息或 assistant 的 tool_calls。"""
    return any(m.is_tool_message() or m.has_tool_calls() for m in messages)


def strip_tool_messages(messages: List[ChatMessage]) -> List[ChatMessage]:
    """
    给现有 chat 链路用：去掉 role=tool 的消息；
    含 tool_calls 的 assistant 消息保留 content（去掉 tool_calls 字段）；
    若 strip 后 content 为空且无 tool_calls 则丢弃。
    """
    out: List[ChatMessage] = []
    for m in messages:
        if m.is_tool_message():
            continue
        if m.has_tool_calls():
            # 保留 text，丢 tool_calls；content 为空就整条丢弃
            if m.content.strip():
                out.append(ChatMessage(role=m.role, content=m.content))
            # else: 整条丢
            continue
        out.append(m)
    return out


# ================================================================
# tool_result 截断（不做 schema 化压缩）
# ================================================================

DEFAULT_MAX_TOOL_RESULT_BYTES = 2048
DEFAULT_HEAD_BYTES = 1024
DEFAULT_TAIL_BYTES = 512

# 本模块自己产出的省略标记。截断结果会再次经过截断（落库前一次、读历史时又一次），
# 认出自己的标记才能保证幂等：已截断的文本再截一次原样返回，而不是把省略计数改写。
_TOOL_OMIT_MARKER_RE = re.compile(r"\n\n\[\.\.\.\d+ bytes omitted by truncate\.\.\.\]\n\n")
# 标记本身的字节数上限（数字位数有限），用于判断「已截断且只超出一个标记的长度」。
_TOOL_OMIT_MARKER_MAX_BYTES = 128
_ASSISTANT_OMIT_MARKER_RE = re.compile(r" \[\.\.\.\d+ chars omitted\.\.\.\] ")


def truncate_tool_result_text(
    text: str,
    max_bytes: int = DEFAULT_MAX_TOOL_RESULT_BYTES,
    head_bytes: int = DEFAULT_HEAD_BYTES,
    tail_bytes: int = DEFAULT_TAIL_BYTES,
) -> str:
    """
    长字符串头尾保留中段截断。

    跨平台 tool 输出 schema 不统一，规则化压缩（"Read 取行数 / Bash 取 exit code"）注定失败；
    最稳妥做法是按字节截断，把信号交给 LLM 自己消化。
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    # 已截断过（max_bytes 很小时标记本身会让结果略超上限）：原样返回，保证幂等。
    if (
        len(raw) <= max_bytes + _TOOL_OMIT_MARKER_MAX_BYTES
        and _TOOL_OMIT_MARKER_RE.search(text)
    ):
        return text
    # 自定义 max_bytes 可能小于默认 head+tail（1024+512）。按原比例缩放内容窗口，
    # 给省略标记预留空间，不能因为窗口和过大而把超限原文直接返回。
    marker_budget = min(96, max(24, max_bytes // 4))
    content_budget = max(2, max_bytes - marker_budget)
    requested = max(1, head_bytes + tail_bytes)
    if head_bytes + tail_bytes > content_budget:
        head_bytes = max(1, int(content_budget * head_bytes / requested))
        tail_bytes = max(1, content_budget - head_bytes)
    head = raw[:head_bytes].decode("utf-8", errors="ignore")
    tail = raw[-tail_bytes:].decode("utf-8", errors="ignore")
    omitted = len(raw) - head_bytes - tail_bytes
    return (
        head
        + f"\n\n[...{omitted} bytes omitted by truncate...]\n\n"
        + tail
    )


def truncate_tool_message(
    msg: ChatMessage,
    max_bytes: int = DEFAULT_MAX_TOOL_RESULT_BYTES,
) -> ChatMessage:
    """对 role=tool 的消息内容做截断。其它 role 原样返回。"""
    if not msg.is_tool_message():
        return msg
    if len(msg.content.encode("utf-8")) <= max_bytes:
        return msg
    new_content = truncate_tool_result_text(msg.content, max_bytes=max_bytes)
    # 不修改原对象（messages 可能被 caller 复用）
    return ChatMessage(
        role=msg.role,
        content=new_content,
        name=msg.name,
        tool_calls=list(msg.tool_calls),
        tool_call_id=msg.tool_call_id,
        tool_name=msg.tool_name,
    )


def truncate_messages(
    messages: List[ChatMessage],
    max_bytes: int = DEFAULT_MAX_TOOL_RESULT_BYTES,
) -> List[ChatMessage]:
    """对整个 messages 列表的所有 tool 消息做截断。"""
    return [truncate_tool_message(m, max_bytes=max_bytes) for m in messages]


# ================================================================
# 长会话窗口压缩：last-K user-turn + assistant 头尾截断
# ================================================================

DEFAULT_LAST_K_USER_TURNS = 5
# 原值是 head 200 + tail 200。那是按"assistant 文本是行文、只需还原意图"设的，
# 但 assistant 的最后一段常常**就是交付物本身**（一份行程表、一段配置、一个答案），
# 对它做头尾截断等于把答卷中间撕掉再让人抄。
#
# 阈值取 6000（head+tail 各 3000）：大多数正常交付物在此长度内可完整保留；超过阈值
# 的通常是反复试错的长轨迹，**仍然要截**——保留它只会稀释注意力。
DEFAULT_ASSISTANT_HEAD_CHARS = 3000
DEFAULT_ASSISTANT_TAIL_CHARS = 3000


def truncate_assistant_text(
    text: str,
    head_chars: int = DEFAULT_ASSISTANT_HEAD_CHARS,
    tail_chars: int = DEFAULT_ASSISTANT_TAIL_CHARS,
) -> str:
    """
    assistant.content 的字符级头尾截断。

    跟 tool_result 不同，assistant 文本一般不会"超大"，但长 trajectory 累积下来
    仍会膨胀 prompt。默认 head 3000 + tail 3000：assistant 的收尾段常常就是交付物
    本身，阈值的取法见模块里 DEFAULT_ASSISTANT_HEAD_CHARS 上方的说明。

    注意：tool_calls 列表不在此截断；在 _format_conversation 中由 caller 决定渲染策略。
    """
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    if len(text) <= head_chars + tail_chars:
        return text
    # 已截断过：中段恰好是本函数的省略标记 → 原样返回，保证幂等（否则会把
    # 省略计数改写成标记自身的长度）。
    if _ASSISTANT_OMIT_MARKER_RE.fullmatch(text[head_chars:len(text) - tail_chars]):
        return text
    omitted = len(text) - head_chars - tail_chars
    return (
        text[:head_chars]
        + f" [...{omitted} chars omitted...] "
        + text[-tail_chars:]
    )


def truncate_assistant_messages(
    messages: List[ChatMessage],
    head_chars: int = DEFAULT_ASSISTANT_HEAD_CHARS,
    tail_chars: int = DEFAULT_ASSISTANT_TAIL_CHARS,
) -> List[ChatMessage]:
    """截断列表内所有 assistant 文本，但不裁掉任何消息或 user-turn。

    ``New Messages`` 必须完整覆盖本次 write 传入的所有 turn；last-K 只属于历史
    上下文。这个 helper 因此只限制单条 assistant 的体积，不做窗口选择。
    """
    out: List[ChatMessage] = []
    for m in messages:
        if m.role != "assistant" or not m.content:
            out.append(m)
            continue
        new_content = truncate_assistant_text(
            m.content,
            head_chars=head_chars,
            tail_chars=tail_chars,
        )
        if new_content == m.content:
            out.append(m)
            continue
        out.append(ChatMessage(
            role=m.role,
            content=new_content,
            name=m.name,
            tool_calls=list(m.tool_calls),
            tool_call_id=m.tool_call_id,
            tool_name=m.tool_name,
        ))
    return out


def prepare_extractor_messages(
    messages: List[ChatMessage],
    *,
    tool_max_bytes: int = DEFAULT_MAX_TOOL_RESULT_BYTES,
    assistant_head_chars: int = DEFAULT_ASSISTANT_HEAD_CHARS,
    assistant_tail_chars: int = DEFAULT_ASSISTANT_TAIL_CHARS,
) -> List[ChatMessage]:
    """限制单条消息体积，同时完整保留输入中的所有 turn。

    供 ``New Messages`` 使用。顺序必须是先裁 tool result，再裁 assistant 文本；
    两步都不改变消息数量和先后关系。
    """
    return truncate_assistant_messages(
        truncate_messages(messages, max_bytes=tool_max_bytes),
        head_chars=assistant_head_chars,
        tail_chars=assistant_tail_chars,
    )


def keep_last_k_user_turns(
    messages: List[ChatMessage],
    k: int = DEFAULT_LAST_K_USER_TURNS,
    assistant_head_chars: int = DEFAULT_ASSISTANT_HEAD_CHARS,
    assistant_tail_chars: int = DEFAULT_ASSISTANT_TAIL_CHARS,
) -> List[ChatMessage]:
    """
    对长 trajectory 压缩为最近 K 个 user-turn 的窗口，并对其中 assistant
    消息做头尾截断。

    一个 "user-turn" 定义为：一条真实 user 消息（非 tool_result-only）+ 之后
    直到下一条真实 user 消息之前的所有 assistant / tool / tool_result 消息。

    设计动机：
    - extractor 的 conversation_block 现状是整段渲染，长 batch 会让 LLM 注意力稀释、
      under-extract
    - tool_result 已有字节截断（head 1024 + tail 512），但 assistant 长文本和
      消息条数本身没有上限
    - 此函数只压缩**消息条数**和 assistant 文本长度；tool_result 由
      truncate_messages 处理，不变

    Args:
        messages: 完整的 ChatMessage 列表（顺序）
        k: 保留最近多少个 user-turn（默认 5）
        assistant_head_chars / assistant_tail_chars: assistant 文本头尾保留字符数

    Returns:
        截断后的 messages 列表（保序，含 tool_call_id 等元字段不变）。
        如果消息为空或 user-turn 数 ≤ k，原样返回（仍会做 assistant 截断以保持行为一致）。
    """
    if not messages:
        return messages

    # 1) 找到所有真实 user-turn 的起始位置（"真实" = role=user 且不是 tool_result-only）
    user_turn_starts: List[int] = []
    for idx, m in enumerate(messages):
        if m.role == "user" and not m.is_tool_message():
            user_turn_starts.append(idx)

    # 2) 决定窗口起点：保留最后 k 个 user-turn → 取倒数第 k 个起点
    if len(user_turn_starts) > k:
        window_start = user_turn_starts[-k]
    else:
        window_start = 0

    windowed = messages[window_start:]

    # 3) 对 assistant 消息做头尾截断（保留 tool_calls）
    out: List[ChatMessage] = []
    for m in windowed:
        if m.role == "assistant" and m.content:
            new_content = truncate_assistant_text(
                m.content,
                head_chars=assistant_head_chars,
                tail_chars=assistant_tail_chars,
            )
            if new_content == m.content:
                out.append(m)
            else:
                out.append(ChatMessage(
                    role=m.role,
                    content=new_content,
                    name=m.name,
                    tool_calls=list(m.tool_calls),
                    tool_call_id=m.tool_call_id,
                    tool_name=m.tool_name,
                ))
        else:
            out.append(m)
    return out


# ================================================================
# 文件路径抽取
# ================================================================

def extract_files(messages: List[ChatMessage]) -> List[str]:
    """
    从 messages 中所有 tool_calls.arguments 抽 path / file_path / filename 等常见键。

    返回去重后排序的 path 列表。跨 OpenAI / Anthropic / Claude Code 都稳定有效，
    属于 tool 信号里少数能规则化处理的部分。
    """
    paths: set = set()
    for m in messages:
        for tc in m.tool_calls:
            if not isinstance(tc.arguments, dict):
                continue
            for k in PATH_KEYS:
                v = tc.arguments.get(k)
                if isinstance(v, str) and v:
                    paths.add(v)
    return sorted(paths)


# ================================================================
# 写入 / search 端 LLM 输入的简化视图
# ================================================================

def extract_tool_summary(messages: List[ChatMessage]) -> List[Dict[str, Any]]:
    """
    给 LLM judge / extractor 的 turn 级简化视图。

    每个 turn = 一个 user 消息 + 之后到下一个 user 之前所有 assistant/tool 消息的 tool_names 集合。
    chat 路径不会调用此函数（无 tool 消息直接走旧链）。

    返回:
        [
            {"turn": 0, "user": "...", "tools": ["Read", "Edit"], "has_tool_result": True},
            ...
        ]
    """
    out: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None

    def _flush():
        if cur is not None:
            # 去重保序
            seen = set()
            cur["tools"] = [t for t in cur["tools"] if not (t in seen or seen.add(t))]
            out.append(cur)

    for m in messages:
        if m.role == "user" and not m.is_tool_message():
            _flush()
            cur = {
                "turn": len(out),
                "user": (m.content or "").strip(),
                "tools": [],
                "has_tool_result": False,
            }
        else:
            if cur is None:
                # 没有 user 起头时（可能是历史 assistant），开一个匿名 turn
                cur = {"turn": len(out), "user": "", "tools": [], "has_tool_result": False}
            if m.has_tool_calls():
                for tc in m.tool_calls:
                    if tc.name:
                        cur["tools"].append(tc.name)
            if m.is_tool_message():
                cur["has_tool_result"] = True
                if m.tool_name:
                    cur["tools"].append(m.tool_name)

    _flush()
    return out
