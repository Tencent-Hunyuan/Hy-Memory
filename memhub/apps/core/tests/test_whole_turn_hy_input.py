"""D74: a turn reaches HY whole; only what HY will read counts toward the batch size."""

from datetime import UTC, datetime

from one_memory.conversation_processing import turn_batches
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore

_WRITER = {"user": "person", "assistant": "agent", "tool": "tool"}


def _turn(tmp_path, *parts: tuple[str, str]) -> list:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    return [
        store.put(
            EvidenceInput(
                source_id="cursor",
                conversation_id="conversation",
                message_id=f"m{index}",
                role=role,
                written_by=WrittenBy(_WRITER[role]),
                verbatim=text,
                created_at=datetime.now(UTC),
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        ).record
        for index, (role, text) in enumerate(parts)
    ]


def test_tool_heavy_turn_is_one_window(tmp_path):
    records = _turn(
        tmp_path,
        ("user", "帮我看看这个仓库的测试为什么挂了"),
        *[("tool", "x" * 50_000) for _ in range(5)],
        *[("assistant", "定位中") for _ in range(30)],
        ("assistant", "原因是 fixture 没清理" * 100),
    )
    batches = turn_batches(records, flush=True).batches
    assert batches == (tuple(records),)


def test_long_answer_is_not_cut(tmp_path):
    records = _turn(tmp_path, ("user", "写一份方案"), ("assistant", "段落。" * 7_000))
    batches = turn_batches(records, flush=True).batches
    assert batches == (tuple(records),)
    assert all(":segment:" not in record.evidence_id for record in batches[0])


def test_a_turn_far_over_the_batch_size_is_still_one_window(tmp_path):
    """The rule never splits a turn: it is written alone, whole.

    The old windows cut text past 60k characters and raw bytes past 2 MB into
    segments that repeated the user question. HY reads at most
    `_HY_TOOL_CHARACTERS` of each tool record, so the window HY reads stays
    bounded by the turn's own text.
    """
    records = _turn(
        tmp_path,
        ("user", "跑一下全部测试"),
        *[("tool", "y" * 400_000) for _ in range(20)],
        ("assistant", "句子。" * 25_000),
    )
    batches = turn_batches(records, flush=True).batches
    assert batches == (tuple(records),)


def test_the_sdk_strips_tool_messages_on_the_chat_path():
    """D74 sends a turn whole partly because HY drops tool records on the chat
    path. If an SDK upgrade stops dropping them, whole turns would start
    sending tool output down a path that never expected it."""
    from hy_memory.coding.preproc import strip_tool_messages
    from hy_memory.pipelines.base import ChatMessage, ToolCall

    question = ChatMessage(role="user", content="帮我看看为什么挂了")
    answer = ChatMessage(role="assistant", content="先看日志")
    tool_call = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[ToolCall(id="c1", name="Bash", arguments={"command": "pytest"})],
    )
    tool = ChatMessage(role="tool", content="x" * 40_000, tool_call_id="c1", tool_name="Bash")

    kept = strip_tool_messages([question, tool_call, tool, answer])

    # The tool record is gone, and so is the assistant message that only
    # carried a tool call and no text.
    assert [item.role for item in kept] == ["user", "assistant"]
    assert all(not item.is_tool_message() for item in kept)
    assert sum(len(item.content or "") for item in kept) < 40_000


def test_the_sdk_still_cuts_tool_results_the_way_this_assumes():
    """MemHub's window estimate follows its configured SDK tool ceiling.

    If an SDK upgrade moves that boundary or stops dropping tool records on
    the chat path, `_HY_TOOL_CHARACTERS` no longer describes what HY reads and
    turns would be measured against the wrong ceiling.
    """
    from hy_memory.coding.preproc import (
        DEFAULT_HEAD_BYTES,
        DEFAULT_MAX_TOOL_RESULT_BYTES,
        DEFAULT_TAIL_BYTES,
        truncate_tool_result_text,
    )
    from hy_memory.pipelines.base import ChatMessage

    from one_memory.conversation_processing import _HY_TOOL_CHARACTERS

    assert DEFAULT_MAX_TOOL_RESULT_BYTES == 2048
    assert _HY_TOOL_CHARACTERS == 512
    long = "z" * 20_000
    cut = truncate_tool_result_text(long, max_bytes=_HY_TOOL_CHARACTERS)
    assert len(cut.encode()) <= _HY_TOOL_CHARACTERS + 128

    from hy_memory.coding.preproc import truncate_messages

    messages = [ChatMessage(role="tool", content=long)]
    truncated = truncate_messages(messages, max_bytes=_HY_TOOL_CHARACTERS)
    assert sum(len(item.content or "") for item in truncated) < len(long)
    assert _HY_TOOL_CHARACTERS < DEFAULT_HEAD_BYTES + DEFAULT_TAIL_BYTES
