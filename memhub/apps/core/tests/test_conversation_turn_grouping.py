"""A proposal and the turn that adopts it must reach HY as one turn."""

from __future__ import annotations

from datetime import UTC, datetime

from one_memory.conversation_processing import _conversation_chunks
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore


def _records(tmp_path, *speakers: tuple[str, str, str]) -> list:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    records = []
    for index, (role, written_by, text) in enumerate(speakers):
        records.append(
            store.put(
                EvidenceInput(
                    source_id="cursor",
                    conversation_id="conversation",
                    message_id=f"m{index}",
                    role=role,
                    written_by=WrittenBy(written_by),
                    verbatim=text,
                    created_at=datetime.now(UTC),
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            ).record
        )
    return records


def test_an_unanswered_assistant_prefix_joins_the_turn_that_adopts_it(tmp_path) -> None:
    """`assistant` proposal -> `user` explicit adoption is one turn, not two.

    Splitting it sends HY an answer without what it answers, and loses the
    authority pair the author/adoption quality gate depends on.
    """
    records = _records(
        tmp_path,
        ("assistant", "agent", "建议把超时改成 30 秒。"),
        ("user", "person", "就这么做，超时改成 30 秒。"),
    )

    chunks = _conversation_chunks(records)

    assert [[record.role for record in chunk] for chunk in chunks] == [["assistant", "user"]]


def test_a_lone_user_message_does_not_become_a_model_input(tmp_path) -> None:
    """A user message with no reply yet is Evidence, not a completed turn.

    Concurrent-import fixtures write `user` then `assistant` pairs, but a lone
    user message must not create an HY task: that is the complete-turn rule,
    not a queue stall.
    """
    records = _records(tmp_path, ("user", "person", "把超时改成 30 秒。"))

    assert _conversation_chunks(records, completed_only=True) == []


def test_only_the_unanswered_trailing_turn_is_held_back(tmp_path) -> None:
    """Earlier complete turns stay eligible while the open tail waits."""
    records = _records(
        tmp_path,
        ("user", "person", "先把超时改成 30 秒。"),
        ("assistant", "agent", "已经改好了。"),
        ("user", "person", "接着把发布包传上去。"),
    )

    assert _conversation_chunks(records, completed_only=True) == [(records[0], records[1])]


def test_the_complete_turn_contract_used_for_concurrent_import_reds(tmp_path) -> None:
    """Node-id level answer for `[BLOCK C2 -> C1]` concurrent-import reds.

    Those fixtures write `user` turns and never emit the closing
    `assistant`/tool event, so under the complete-turn rule no HY task is
    created inside the 5s window. That is the contract, not a queue stall:
    Evidence is still committed and searchable, and the task appears once the
    closing event arrives.
    """
    closed = _records(
        tmp_path,
        ("user", "person", "把超时改成 30 秒。"),
        ("assistant", "agent", "已改为 30 秒。"),
    )
    assert [[record.role for record in chunk] for chunk in _conversation_chunks(closed)] == [
        ["user", "assistant"]
    ]
