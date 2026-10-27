"""The turn rule: what a conversation's records become before they reach HY.

A turn is one user message and everything up to the next one. Every 5
complete turns are written together, sooner when the next turn would pass
8000 characters; a short batch waits for the end of a history file or 10
quiet minutes. No batch is written without a user message.
"""

from __future__ import annotations

from datetime import UTC, datetime

from one_memory.conversation_processing import (
    TURN_BATCH_CHARACTERS,
    TURN_BATCH_TURNS,
    conversation_turns,
    turn_batches,
)
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore

_WRITER = {"user": "person", "assistant": "agent", "tool": "tool"}


def _records(tmp_path, *speakers: tuple[str, str]) -> list:
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
        for index, (role, text) in enumerate(speakers)
    ]


def _turns(count: int, *, answer: str = "好的。") -> list[tuple[str, str]]:
    return [
        part
        for index in range(count)
        for part in (("user", f"第 {index} 个问题"), ("assistant", answer))
    ]


def _roles(batches) -> list[list[str]]:
    return [[record.role for record in batch] for batch in batches]


def test_rule_constants_are_the_product_rule() -> None:
    assert (TURN_BATCH_TURNS, TURN_BATCH_CHARACTERS) == (5, 8_000)


def test_a_turn_runs_from_one_user_message_to_the_next(tmp_path) -> None:
    records = _records(
        tmp_path,
        ("user", "跑一下测试"),
        ("assistant", "好"),
        ("tool", "3 passed"),
        ("assistant", "都通过了"),
        ("user", "提交吧"),
        ("assistant", "已提交"),
    )

    assert [len(turn) for turn in conversation_turns(records)] == [4, 2]


def test_records_before_the_first_user_message_join_the_first_turn(tmp_path) -> None:
    """An assistant proposal and the user turn adopting it reach HY together."""
    records = _records(
        tmp_path,
        ("assistant", "建议把超时改成 30 秒。"),
        ("user", "就这么做，超时改成 30 秒。"),
        ("assistant", "改好了。"),
        ("user", "再把重试改成 3 次。"),
        ("assistant", "也改好了。"),
    )

    assert _roles(conversation_turns(records)) == [
        ["assistant", "user", "assistant"],
        ["user", "assistant"],
    ]


def test_five_complete_turns_are_one_batch_and_the_open_turn_waits(tmp_path) -> None:
    records = _records(tmp_path, *_turns(6))

    grouped = turn_batches(records, flush=False)

    assert [len(batch) for batch in grouped.batches] == [10]
    assert grouped.batches[0] == tuple(records[:10])
    assert grouped.carry == tuple(records[10:])


def test_complete_turns_short_of_five_wait_for_flush(tmp_path) -> None:
    records = _records(tmp_path, *_turns(3))

    waiting = turn_batches(records, flush=False)
    assert waiting.batches == ()
    assert waiting.carry == tuple(records)

    flushed = turn_batches(records, flush=True)
    assert flushed.batches == (tuple(records),)
    assert flushed.carry == ()


def test_a_lone_user_message_is_not_written_until_flush(tmp_path) -> None:
    records = _records(tmp_path, ("user", "把超时改成 30 秒。"))

    assert turn_batches(records, flush=False).batches == ()
    assert turn_batches(records, flush=True).batches == (tuple(records),)


def test_the_turn_that_would_pass_8000_characters_starts_the_next_batch(tmp_path) -> None:
    answer = "句" * 3_000
    records = _records(tmp_path, *_turns(4, answer=answer))

    grouped = turn_batches(records, flush=False)

    # Turns 1+2 fit; turn 3 would pass 8000, so 1+2 are written now. Turn 3
    # is complete but alone, and turn 4 is still open.
    assert grouped.batches == (tuple(records[:4]),)
    assert grouped.carry == tuple(records[4:])
    assert sum(len(record.verbatim) for record in grouped.batches[0]) <= TURN_BATCH_CHARACTERS


def test_one_turn_over_8000_characters_is_a_batch_of_its_own_and_not_split(tmp_path) -> None:
    records = _records(
        tmp_path,
        ("user", "先问个小问题"),
        ("assistant", "好"),
        ("user", "写一份方案"),
        ("assistant", "段落。" * 7_000),
        ("user", "谢谢"),
    )

    grouped = turn_batches(records, flush=False)

    assert grouped.batches == (tuple(records[:2]), tuple(records[2:4]))
    assert grouped.carry == (records[4],)
    assert all(":segment:" not in record.evidence_id for record in grouped.batches[1])


def test_tool_output_counts_only_what_hy_reads(tmp_path) -> None:
    """A tool record counts up to `_HY_TOOL_CHARACTERS`, not its full text."""
    from one_memory.conversation_processing import _HY_TOOL_CHARACTERS

    parts = [
        part
        for index in range(5)
        for part in (("user", f"第 {index} 步"), ("tool", "x" * 50_000), ("assistant", "完成"))
    ]
    records = _records(tmp_path, *parts, ("user", "下一步"))

    grouped = turn_batches(records, flush=False)

    assert _HY_TOOL_CHARACTERS == 512
    assert [len(batch) for batch in grouped.batches] == [15]


def test_records_with_no_user_message_are_never_a_batch(tmp_path) -> None:
    """The SDK strips tool records on the chat path and refuses what is left.

    A tool-only batch came back 400 "content or messages is required" and
    was recorded unknown, 16 times. Carried until a user message arrives;
    dropped when the conversation is flushed without one.
    """
    records = _records(tmp_path, ("tool", "ls output"), ("assistant", "看完了"))

    waiting = turn_batches(records, flush=False)
    assert waiting.batches == ()
    assert waiting.carry == tuple(records)
    assert turn_batches(records, flush=True) == turn_batches((), flush=True)

    later = _records(tmp_path, ("user", "那就开始吧"), ("assistant", "开始"))
    joined = turn_batches((*waiting.carry, *later), flush=True)
    assert _roles(joined.batches) == [["tool", "assistant", "user", "assistant"]]


def test_every_batch_holds_a_user_message(tmp_path) -> None:
    records = _records(
        tmp_path,
        ("tool", "前置输出"),
        *_turns(7, answer="答" * 2_500),
        ("tool", "尾部输出"),
    )

    for flush in (False, True):
        grouped = turn_batches(records, flush=flush)
        assert grouped.batches
        assert all(any(record.role == "user" for record in batch) for batch in grouped.batches)
        written = [record for batch in grouped.batches for record in batch]
        assert written + list(grouped.carry) == records
