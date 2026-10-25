"""A7-03: the fault matrix is complete, and the dangerous outcomes are pinned."""

from __future__ import annotations

from one_memory.memory_server.fault_matrix import (
    REQUIRED_NAMES,
    Outcome,
    missing,
    names,
    outcome_of,
)


def test_the_matrix_is_complete() -> None:
    assert missing() == ()
    assert set(names()) == set(REQUIRED_NAMES)


def test_every_fault_has_an_outcome() -> None:
    for name in names():
        assert outcome_of(name) is not None


def test_a_crash_after_hy_success_is_unknown_not_failed() -> None:
    """Recording failure would lose a memory that may exist."""
    assert outcome_of("hy_succeeded_then_crash") == Outcome.UNKNOWN_RECORDED


def test_an_interrupted_call_is_inquired_not_retried() -> None:
    """Retrying blind duplicates it (A0-03: no idempotency key)."""
    action = outcome_of("hy_call_interrupted")
    assert action == Outcome.INQUIRE
    assert action != Outcome.RETRY


def test_a_pre_send_failure_may_retry() -> None:
    assert outcome_of("hy_fails_before_send") == Outcome.RETRY


def test_disk_full_refuses_writes() -> None:
    """A half-applied change is worse than a pause."""
    assert outcome_of("disk_full") == Outcome.REFUSE_WRITES


def test_file_rotation_re_resolves_identity() -> None:
    """The old offset now points into a different file."""
    assert outcome_of("file_rotation") == Outcome.RE_RESOLVE_IDENTITY


def test_mysql_restart_recovers_by_replay() -> None:
    assert outcome_of("mysql_restart") == Outcome.RECOVER_BY_REPLAY


def test_unknown_fault_names_are_not_silently_accepted() -> None:
    assert outcome_of("not_a_real_fault") is None


def test_each_fault_outcome_is_realised_by_the_durable_queue(tmp_path) -> None:
    """The matrix must describe the queue, not just declare good intentions.

    Every fault above names a required outcome. This drives the real
    `DurableWriteQueue` through the ones that live in this chain, so the table
    cannot drift away from the implementation it is supposed to pin.
    """
    import asyncio
    import sqlite3

    from one_memory.memory_server.contracts import BatchIdentity, WriteRequest
    from one_memory.memory_server.durable_queue import (
        CANCELLED,
        UNKNOWN,
        WAITING_FOR_PERMISSION,
        DurableWriteDrain,
        DurableWriteQueue,
        execute_job,
    )

    def request(key: str) -> WriteRequest:
        return WriteRequest(
            internal_session_id="sess-1",
            batch_identity=BatchIdentity(internal_session_id="sess-1", first_round=1, last_round=1),
            evidence_ids=["e1"],
            idempotency_key=key,
        )

    class _Evidence:
        def get(self, evidence_id: str):
            return type("R", (), {"evidence_id": evidence_id})()

    class _Supervisor:
        def __init__(self, error: Exception | None = None) -> None:
            self.error = error
            self.seen: list[str] = []

        async def process_sdk_job(self, records, **_kwargs):
            # Refusals are classified before `before_hy_invocation`, as the
            # SDK write processor does, so these never cross into HY.
            self.seen.append("called")
            if self.error is not None:
                raise self.error
            return type("Result", (), {"raw_ids": (), "memory_ids": ()})()

    # hy_succeeded_then_crash: marked invoked, then the lease lapses -> unknown.
    path = tmp_path / "fault.sqlite3"
    queue = DurableWriteQueue(path)
    crashed = queue.submit(request("crash"))
    DurableWriteDrain(queue, execute=lambda _job: asyncio.sleep(0)).claim(limit=1)
    queue.mark_invoked(crashed.job_id)
    with sqlite3.connect(path) as connection, connection:
        connection.execute(
            "UPDATE memory_write_jobs SET lease_expires_at='2000-01-01T00:00:00+00:00'"
        )
    assert DurableWriteQueue(path).get("crash").state == UNKNOWN
    assert outcome_of("hy_succeeded_then_crash") == Outcome.UNKNOWN_RECORDED

    # hy_fails_before_send: a pre-bridge refusal is parked, never sent.
    queue = DurableWriteQueue(tmp_path / "fault2.sqlite3")
    before = queue.submit(request("before"))
    DurableWriteDrain(queue, execute=lambda _job: asyncio.sleep(0)).claim(limit=1)
    supervisor = _Supervisor(RuntimeError("model_content_permission_required"))
    asyncio.run(
        execute_job(before.job_id, queue=queue, supervisor=supervisor, evidence=_Evidence())
    )
    assert supervisor.seen == ["called"]
    assert queue.get("before").state == WAITING_FOR_PERMISSION
    assert outcome_of("hy_fails_before_send") == Outcome.RETRY

    # A revoked policy is a user stop, not a failed pipeline.
    queue = DurableWriteQueue(tmp_path / "fault3.sqlite3")
    stopped = queue.submit(request("stopped"))
    DurableWriteDrain(queue, execute=lambda _job: asyncio.sleep(0)).claim(limit=1)
    asyncio.run(
        execute_job(
            stopped.job_id,
            queue=queue,
            supervisor=_Supervisor(RuntimeError("model_content_permission_revoked")),
            evidence=_Evidence(),
        )
    )
    assert queue.get("stopped").state == CANCELLED
