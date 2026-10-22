"""BUG-06: which conversations end on a question nobody answered.

A turn is a question together with its answer. Extraction must not draw a
memory out of a turn that is still open, so the one thing that has to be right
is where the conversation stops.
"""

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore, last_row_opens_an_unanswered_turn


def test_a_question_with_nothing_after_it_is_unanswered():
    assert last_row_opens_an_unanswered_turn(("user", "person", "打包好了吗")) is True


def test_an_assistant_reply_closes_the_turn():
    assert last_row_opens_an_unanswered_turn(("assistant", "agent", "已经好了")) is False


def test_a_tool_result_closes_the_turn():
    # The assistant was mid-work. Reading that as silence would mark most real
    # conversations incomplete.
    assert last_row_opens_an_unanswered_turn(("tool", "agent", "build ok")) is False


def test_a_heartbeat_is_not_a_question():
    # Same filter the turn counter uses: a heartbeat is noise, not someone
    # asking something.
    assert last_row_opens_an_unanswered_turn(("user", "person", "<heartbeat>ping")) is False


def test_protocol_user_role_opens_a_turn_regardless_of_legacy_author_guess():
    assert last_row_opens_an_unanswered_turn(("user", "agent", "继续")) is True


def test_a_conversation_with_no_rows_is_not_unanswered():
    assert last_row_opens_an_unanswered_turn(None) is False


def test_turn_page_does_not_use_a_reader_after_returning_it_to_the_pool(tmp_path, monkeypatch):
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    record = store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="lease-bound-reader",
            message_id="question",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="打包好了吗",
            created_at=datetime.now(UTC),
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    original_connect = store._connect

    class LeaseBoundConnection:
        def __init__(self, connection):
            self.connection = connection
            self.active = True

        def __getattr__(self, name):
            if not self.active:
                raise RuntimeError("reader_used_after_return_to_pool")
            return getattr(self.connection, name)

    @contextmanager
    def guarded_connect(*, initialize=False, writing=False):
        with original_connect(initialize=initialize, writing=writing) as connection:
            guarded = LeaseBoundConnection(connection)
            try:
                yield guarded
            finally:
                guarded.active = False

    monkeypatch.setattr(store, "_connect", guarded_connect)

    page = store.list_conversation_turns(record.evidence_id)

    assert page.trailing_turn_unanswered is True


if __name__ == "__main__":
    pytest.main([__file__])
