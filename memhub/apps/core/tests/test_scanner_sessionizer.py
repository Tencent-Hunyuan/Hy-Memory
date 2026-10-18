"""A4-05: session and round derivation.

The cases that matter are the ones a naive "one message = one round" or
"new file = new session" implementation gets wrong, because those mistakes
silently produce wrong memories rather than raising.
"""

from __future__ import annotations

from one_memory.memory_server.contracts import CanonicalMessage, SessionIdentity
from one_memory.memory_server.scanner.sessionizer import complete_rounds, sessionize


def _message(
    ordinal: int,
    role: str = "user",
    *,
    content: str = "hi",
    external_session_id: str = "s1",
    message_id: str | None = None,
) -> CanonicalMessage:
    return CanonicalMessage(
        harness_id="cursor",
        external_session_id=external_session_id,
        message_id=message_id or f"m{ordinal}",
        ordinal=ordinal,
        role=role,
        content=content,
    )


def test_one_question_and_answer_is_one_round() -> None:
    rounds = sessionize(
        [_message(0), _message(1, "assistant")], harness_id="cursor", source_file_key="f" * 64
    )
    assert len(rounds) == 1
    assert rounds[0].is_complete is True


def test_a_round_is_not_complete_until_answered() -> None:
    """A trailing user turn is still being written; it must not be batched."""
    rounds = sessionize([_message(0)], harness_id="cursor", source_file_key="f" * 64)
    assert len(rounds) == 1
    assert rounds[0].is_complete is False
    assert complete_rounds(list(rounds)) == 0


def test_consecutive_user_messages_stay_in_one_round() -> None:
    """An unanswered user turn is still the same turn, not a finished round."""
    rounds = sessionize(
        [_message(0), _message(1), _message(2, "assistant")],
        harness_id="cursor",
        source_file_key="f" * 64,
    )
    assert len(rounds) == 1
    assert [message.ordinal for message in rounds[0].messages] == [0, 1, 2]


def test_a_new_user_turn_after_an_answer_starts_a_new_round() -> None:
    rounds = sessionize(
        [
            _message(0),
            _message(1, "assistant"),
            _message(2),
            _message(3, "assistant"),
        ],
        harness_id="cursor",
        source_file_key="f" * 64,
    )
    assert len(rounds) == 2
    assert [item.round_number for item in rounds] == [1, 2]
    assert complete_rounds(list(rounds)) == 2


def test_messages_are_ordered_by_ordinal_not_arrival() -> None:
    """Adapters may emit out of order; ordinal is the truth for a conversation."""
    rounds = sessionize(
        [_message(3, "assistant"), _message(0)], harness_id="cursor", source_file_key="f" * 64
    )
    assert [message.ordinal for message in rounds[0].messages] == [0, 3]


def test_different_external_sessions_are_separate() -> None:
    rounds = sessionize(
        [
            _message(0, external_session_id="a"),
            _message(1, "assistant", external_session_id="b"),
        ],
        harness_id="cursor",
        source_file_key="f" * 64,
    )
    assert len({item.internal_session_id for item in rounds}) == 2


def test_cross_file_session_continues_round_numbering() -> None:
    """A session spanning files must not restart at round 1 in the second file.

    Restarting would reissue batch ids that already exist, so the dedupe would
    either drop new work as duplicate or write a second copy.
    """
    first = sessionize(
        [_message(0), _message(1, "assistant")], harness_id="cursor", source_file_key="f" * 64
    )
    session_id = first[0].internal_session_id
    second = sessionize(
        [_message(2), _message(3, "assistant")],
        harness_id="cursor",
        source_file_key="g" * 64,
        last_committed_round_by_session={session_id: 1},
    )
    assert second[0].internal_session_id == session_id
    assert second[0].round_number == 2


def test_missing_external_session_id_derives_a_stable_session() -> None:
    first = sessionize(
        [_message(0, external_session_id="")], harness_id="cursor", source_file_key="f" * 64
    )
    again = sessionize(
        [_message(0, external_session_id="")], harness_id="cursor", source_file_key="f" * 64
    )
    assert first[0].internal_session_id == again[0].internal_session_id
    assert (
        first[0].internal_session_id
        == SessionIdentity.derive(
            harness_id="cursor", external_session_id=None, source_file_key="f" * 64
        ).internal_session_id
    )


def test_harness_id_participates_in_identity() -> None:
    """Two harnesses emitting the same external id must not collide."""
    cursor = sessionize(
        [_message(0, external_session_id="same")], harness_id="cursor", source_file_key="f" * 64
    )
    codex = sessionize(
        [_message(0, external_session_id="same")], harness_id="codex", source_file_key="f" * 64
    )
    assert cursor[0].internal_session_id != codex[0].internal_session_id


def test_empty_input_produces_nothing() -> None:
    assert sessionize([], harness_id="cursor", source_file_key="f" * 64) == ()


def test_round_numbers_are_absolute_within_a_session() -> None:
    rounds = sessionize(
        [
            _message(0),
            _message(1, "assistant"),
            _message(2),
            _message(3, "assistant"),
            _message(4),
            _message(5, "assistant"),
        ],
        harness_id="cursor",
        source_file_key="f" * 64,
    )
    assert [item.round_number for item in rounds] == [1, 2, 3]
