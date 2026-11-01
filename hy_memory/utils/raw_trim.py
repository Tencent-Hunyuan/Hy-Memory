# -*- coding: utf-8 -*-
"""
原始对话（raw）落库前的统一裁剪规则（规则，零 LLM）。

raw 会被长期保存（MemHub 已取消 raw 的保留期限；lite 模式下 raw 是唯一存储），
不裁剪会把工具输出、大段粘贴日志原样存下来。因此 raw 在**写入时**就裁好，
读取方（extractor 的「最近 k 条」历史注入）直接拼接，不再二次裁剪。

规则按说话人标签（ChatMessage.speaker_label() / L1_RAW 里的 ``[label]``）：

- ``user``：完整保留；
- ``system``：完整保留（与历史注入原口径一致）；
- ``tool``：头尾保留、中段替换为 ``[...N bytes omitted by truncate...]``，
  上限为 ``coding.tool_result_max_bytes``（env ``MEMORY_CODING_TOOL_RESULT_MAX_BYTES``，
  默认 2048），与 coding 链路同一个函数；
- 其余（``assistant``、对方真人名字等非已知 role 的 label）：strip 后超过
  ``assistant_max_chars``（默认 500，即历史注入原来的 ``max_chars_assistant``）
  时截成前 N 个字符 + ``...``。

所有规则确定且幂等：对已裁剪的文本再裁一次结果不变。
"""

from typing import List, Optional

from ..pipelines.base import ChatMessage

# 历史注入原 `_get_recent_history(max_chars_assistant=500)` 的默认值，现为唯一来源。
DEFAULT_RAW_ASSISTANT_MAX_CHARS = 500

# L1_RAW 节点 custom 上的标记：内容已按本模块规则在写入时裁剪。
# 历史拼接据此区分「新 raw 直接拼接」与「本规则之前写入的旧 raw 需兼容裁剪」。
RAW_TRIMMED_KEY = "raw_trimmed"

# 不裁剪的标签（tool 单独处理）
_KEEP_LABELS = frozenset({"user", "system"})


def trim_raw_body(
    label: str,
    body: str,
    *,
    assistant_max_chars: int = DEFAULT_RAW_ASSISTANT_MAX_CHARS,
    tool_max_bytes: Optional[int] = None,
) -> str:
    """按说话人标签裁剪单条消息正文。幂等。"""
    if not isinstance(body, str):
        body = "" if body is None else str(body)
    if label in _KEEP_LABELS:
        return body
    if label == "tool":
        from ..coding.preproc import (
            DEFAULT_MAX_TOOL_RESULT_BYTES,
            truncate_tool_result_text,
        )

        return truncate_tool_result_text(
            body, max_bytes=int(tool_max_bytes or DEFAULT_MAX_TOOL_RESULT_BYTES),
        )
    stripped = body.strip()
    if len(stripped) > assistant_max_chars:
        return stripped[:assistant_max_chars] + "..."
    return body


def trim_raw_messages(
    messages: List[ChatMessage],
    *,
    assistant_max_chars: int = DEFAULT_RAW_ASSISTANT_MAX_CHARS,
    tool_max_bytes: Optional[int] = None,
) -> List[ChatMessage]:
    """返回裁剪后的新列表；不修改入参（messages 仍要原样给 extractor）。"""
    out: List[ChatMessage] = []
    for m in messages:
        new_content = trim_raw_body(
            m.speaker_label(),
            m.content,
            assistant_max_chars=assistant_max_chars,
            tool_max_bytes=tool_max_bytes,
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
