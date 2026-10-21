"""A0-02: freeze the cross-layer write contract (red state first).

docs/ARCHITECTURE.md A0-02 requires freezing `WriteRequest`, CanonicalMessage,
session identity, batch identity and the task state machine *before* A1/A2
change the storage engine. The reason is specific: today a session is
identified by whatever `conversation_id` the Harness happened to emit, and
"every five rounds" is counted per file. Under the target design a session can
span files and a file can hold several sessions, so both identities must be
derived, versioned and stable before anything enqueues against them.

These tests are written against the contract module that does not exist yet,
so they fail first. That is the intended red state.
"""

from __future__ import annotations

import pytest

from one_memory.memory_server.contracts import (
    SCHEMA_VERSION,
    BatchIdentity,
    CanonicalMessage,
    SessionIdentity,
    WriteJobState,
    WriteRequest,
    batch_id,
    session_id,
)


def test_internal_session_id_is_derived_and_versioned() -> None:
    """Same harness + same external id must always yield the same internal id.

    `workbuddy_<sid>` style strings are display labels, not authority: two
    harnesses can emit the same external id and must not collide.
    """
    first = session_id(harness_id="workbuddy", external_session_id="abc")
    second = session_id(harness_id="workbuddy", external_session_id="abc")
    other = session_id(harness_id="cursor", external_session_id="abc")

    assert first == second
    assert first != other
    assert first.startswith(SCHEMA_VERSION)


def test_missing_external_session_id_still_yields_a_stable_session() -> None:
    """A harness with no session id cannot be dropped, so it gets a derived one.

    The identity has to come from something stable about the file, otherwise
    every scan invents a new session and the five-round batch never fills.

    With no host session id there is nothing to hash, so the HMAC field is
    empty by design rather than being filled with a hash of the empty string,
    which would be indistinguishable from a real (absurd) session id.
    """
    identity = SessionIdentity.derive(
        harness_id="codex",
        external_session_id=None,
        source_file_key="a" * 64,
    )
    again = SessionIdentity.derive(
        harness_id="codex",
        external_session_id=None,
        source_file_key="a" * 64,
    )
    assert identity.internal_session_id == again.internal_session_id
    assert identity.external_session_id_hmac == ""
    assert identity.internal_session_id != session_id(
        harness_id="codex", external_session_id="a" * 64
    )


def test_batch_id_is_deterministic_for_the_same_rounds() -> None:
    """The same session and round range must produce one id, forever.

    This is what makes a replayed scan safe: re-deriving the id and finding it
    already committed is how "already enqueued" is known without a second
    source of truth.
    """
    first = batch_id(internal_session_id="sess-1", first_round=1, last_round=5)
    second = batch_id(internal_session_id="sess-1", first_round=1, last_round=5)
    third = batch_id(internal_session_id="sess-1", first_round=6, last_round=10)

    assert first == second
    assert first != third


def test_write_request_requires_one_authoritative_input() -> None:
    with pytest.raises(ValueError):
        WriteRequest(
            internal_session_id="sess-1",
            batch_identity=BatchIdentity(
                internal_session_id="sess-1",
                first_round=1,
                last_round=5,
            ),
            evidence_ids=[],
            idempotency_key="key-1",
        )
    raw = WriteRequest(
        internal_session_id="sess-1",
        source_id="cursor",
        batch_identity=BatchIdentity(
            internal_session_id="sess-1",
            first_round=1,
            last_round=5,
        ),
        raw_ids=["raw-1", "raw-2"],
        idempotency_key="key-raw",
    )
    assert raw.evidence_ids == []
    assert raw.input_records == ()
    assert raw.raw_ids == ["raw-1", "raw-2"]
    with pytest.raises(ValueError):
        WriteRequest(
            internal_session_id="sess-1",
            source_id="cursor",
            batch_identity=raw.batch_identity,
            evidence_ids=["evidence-1"],
            raw_ids=["raw-1"],
            idempotency_key="key-both",
        )


def test_write_job_state_covers_unknown_and_terminal() -> None:
    """`unknown` is the honest answer when HY succeeded but the write did not land.

    A state machine without it forces either a blind retry (duplicate memory)
    or a silent drop (lost memory).

    `cancelled` is terminal too: a user cancellation must not be quietly
    picked up again by the next claimer. `unknown` deliberately is not
    terminal, because it is exactly the state that still needs resolving.
    """
    assert WriteJobState.UNKNOWN.value == "unknown"
    assert {
        WriteJobState.COMPLETED,
        WriteJobState.FAILED,
        WriteJobState.CANCELLED,
        WriteJobState.DEAD,
    } == {state for state in WriteJobState if state.is_terminal}
    assert not WriteJobState.UNKNOWN.is_terminal
    assert {
        WriteJobState.PENDING,
        WriteJobState.RUNNING,
        WriteJobState.UNKNOWN,
    } == {state for state in WriteJobState if not state.is_terminal}


def test_canonical_message_requires_a_nonnegative_ordinal() -> None:
    with pytest.raises(ValueError):
        CanonicalMessage(
            harness_id="cursor",
            external_session_id="s1",
            message_id=None,
            ordinal=-1,
            role="user",
            content="hello",
        )
