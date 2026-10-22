import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier
from uuid import UUID

import pytest
from pydantic import ValidationError

requests_module = pytest.importorskip(
    "one_memory.processing_requests",
    reason="processing-request store is not part of the public Core snapshot",
)
from one_memory.processing_requests import (  # noqa: E402 - importorskip must run first
    ProcessingRequest,
    ProcessingRequestConflictError,
    ProcessingRequestState,
    ProcessingRequestStore,
)


def create(store: ProcessingRequestStore, **changes: object) -> ProcessingRequest:
    arguments = {
        "user_id": "local",
        "permission_revision": 1,
        "revocation_revision": 0,
        "profile_id": "test-profile",
        "profile_revision": 1,
    }
    arguments.update(changes)
    return store.create_or_get(**arguments)  # type: ignore[arg-type]


def test_receipt_is_small_frozen_and_persistent(tmp_path: Path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    store = ProcessingRequestStore(database)
    request = create(store)

    assert str(UUID(request.request_id)) == request.request_id
    assert request.state is ProcessingRequestState.QUEUED
    assert request.safe_error_code is None
    assert request.created_at == request.updated_at
    assert request.created_at.utcoffset() == UTC.utcoffset(request.created_at)
    assert len(request.model_dump_json()) < 700
    assert create(ProcessingRequestStore(database)) == request
    assert store.get(request.request_id, user_id="other") is None
    assert store.get(request.request_id, user_id="local") == request
    assert (
        store.get_for_permission(user_id="local", permission_revision=1, revocation_revision=0)
        == request
    )
    with pytest.raises(ValidationError):
        request.state = ProcessingRequestState.FAILED
    with pytest.raises(ValidationError):
        ProcessingRequest.model_validate({**request.model_dump(), "body": "not permitted"})


def test_grant_deduplication_never_retriggers_or_changes_profile(tmp_path: Path) -> None:
    store = ProcessingRequestStore(tmp_path / "state.sqlite3")
    initial = create(store)
    preparing = store.mark_preparing(initial)
    prepared = store.mark_prepared(preparing)

    assert create(store) == prepared
    assert store.list_resumable() == []
    with pytest.raises(ProcessingRequestConflictError, match="processing_request_scope_mismatch"):
        create(store, profile_id="other-profile")
    with pytest.raises(ProcessingRequestConflictError, match="processing_request_scope_mismatch"):
        create(store, profile_revision=2)
    with pytest.raises(ProcessingRequestConflictError):
        store.mark_preparing(prepared)
    with pytest.raises(ProcessingRequestConflictError):
        store.interrupt(prepared)
    assert store.get(initial.request_id, user_id="local") == prepared
    assert (
        len(
            {
                initial.request_id,
                create(store, permission_revision=2).request_id,
                create(store, revocation_revision=1).request_id,
                create(store, user_id="other").request_id,
            }
        )
        == 4
    )


def test_concurrent_create_same_grant_has_one_id_and_profile(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    stores = [ProcessingRequestStore(database) for _ in range(8)]
    barrier = Barrier(len(stores))

    def submit(store: ProcessingRequestStore) -> ProcessingRequest:
        barrier.wait(timeout=5)
        return create(store)

    with ThreadPoolExecutor(max_workers=len(stores)) as executor:
        requests = list(executor.map(submit, stores))
    assert all(request == requests[0] for request in requests)
    assert stores[0].list_resumable() == [requests[0]]


def test_concurrent_claim_only_one_snapshot_wins(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    stores = [ProcessingRequestStore(database) for _ in range(2)]
    initial = create(stores[0])
    barrier = Barrier(2)

    def claim(store: ProcessingRequestStore) -> ProcessingRequest | None:
        barrier.wait(timeout=5)
        try:
            return store.mark_preparing(initial)
        except ProcessingRequestConflictError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, stores))
    assert sum(result is not None for result in results) == 1


def test_stale_callbacks_cannot_overwrite_terminal_or_retried_work(tmp_path: Path) -> None:
    store = ProcessingRequestStore(tmp_path / "state.sqlite3")
    queued = create(store)
    preparing = store.mark_preparing(queued)
    failed = store.mark_failed(preparing, safe_error_code="model_unavailable")
    assert create(store) == failed
    assert store.list_resumable() == []
    retry = store.retry(failed)
    next_preparing = store.mark_preparing(retry)
    assert queued.request_id == next_preparing.request_id
    assert queued.created_at == next_preparing.created_at
    assert queued.updated_at < preparing.updated_at < failed.updated_at < retry.updated_at
    assert retry.safe_error_code is None
    with pytest.raises(ProcessingRequestConflictError):
        store.mark_prepared(preparing)
    with pytest.raises(ProcessingRequestConflictError):
        store.mark_failed(preparing, safe_error_code="late_failure")
    prepared = store.mark_prepared(next_preparing)
    with pytest.raises(ProcessingRequestConflictError):
        store.interrupt(next_preparing)
    assert store.mark_prepared(prepared) == prepared
    assert create(store) == prepared


def test_startup_recovery_only_interrupts_inflight_preparation(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    store = ProcessingRequestStore(database)
    queued = create(store)
    preparing = store.mark_preparing(create(store, permission_revision=2))
    prepared = store.mark_prepared(store.mark_preparing(create(store, permission_revision=3)))
    failed = store.mark_failed(
        create(store, permission_revision=4), safe_error_code="scope_changed"
    )
    interrupted = store.interrupt(create(store, permission_revision=5))
    reopened = ProcessingRequestStore(database)

    assert reopened.get(preparing.request_id, user_id="local") == preparing
    assert reopened.recover_interrupted() == 2
    assert reopened.recover_interrupted() == 0
    resumable = reopened.list_resumable(user_id="local")
    assert [request.request_id for request in resumable] == [
        queued.request_id,
        preparing.request_id,
        interrupted.request_id,
    ]
    assert all(request.state is ProcessingRequestState.INTERRUPTED for request in resumable)
    assert reopened.list_resumable(user_id="other") == []
    assert reopened.list_resumable(limit=1) == resumable[:1]
    assert reopened.get(prepared.request_id, user_id="local") == prepared
    assert reopened.get(failed.request_id, user_id="local") == failed
    with pytest.raises(ProcessingRequestConflictError):
        reopened.mark_prepared(preparing)
    assert reopened.mark_preparing(resumable[0]).state is ProcessingRequestState.PREPARING


@pytest.mark.parametrize(
    "change",
    [
        {"user_id": ""},
        {"user_id": "hidden\nowner"},
        {"user_id": "x" * 513},
        {"permission_revision": True},
        {"permission_revision": "1"},
        {"permission_revision": 0},
        {"revocation_revision": -1},
        {"revocation_revision": False},
        {"profile_revision": 0},
        {"profile_revision": 2**63},
        {"profile_id": ""},
    ],
)
def test_invalid_scope_is_rejected_without_receipt(tmp_path: Path, change: dict) -> None:
    store = ProcessingRequestStore(tmp_path / "state.sqlite3")
    with pytest.raises((ValueError, TypeError)):
        create(store, **change)
    assert store.list_resumable() == []


@pytest.mark.parametrize(
    "code", ["", "raw body", "https://example.invalid", "API_KEY", "a\nb", "a" * 65]
)
def test_unsafe_error_code_is_rejected_before_write(tmp_path: Path, code: str) -> None:
    store = ProcessingRequestStore(tmp_path / "state.sqlite3")
    request = create(store)
    with pytest.raises(ValidationError):
        store.mark_failed(request, safe_error_code=code)
    assert store.get(request.request_id, user_id="local") == request


def test_forged_or_changed_receipt_scope_and_timestamp_rejected(tmp_path: Path) -> None:
    store = ProcessingRequestStore(tmp_path / "state.sqlite3")
    request = create(store)
    for changes in [
        {"user_id": "other"},
        {"profile_revision": 2},
        {"updated_at": datetime(2001, 1, 1, tzinfo=UTC)},
        {"safe_error_code": "a secret"},
    ]:
        forged = request.model_copy(update=changes)
        with pytest.raises((ProcessingRequestConflictError, ValidationError)):
            store.mark_preparing(forged)
    assert store.get(request.request_id, user_id="local") == request


def test_recovery_rolls_back_all_changes_on_write_failure(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    store = ProcessingRequestStore(database)
    first = create(store)
    second = create(store, permission_revision=2)
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE memory_task_inputs(task_id TEXT, refs_json TEXT)")
        db.execute("INSERT INTO memory_task_inputs VALUES ('existing-task','unchanged')")
        db.execute("""
            CREATE TRIGGER fail_second_recovery BEFORE UPDATE ON processing_requests
            WHEN NEW.permission_revision = 2
            BEGIN SELECT RAISE(ABORT, 'synthetic_write_failure'); END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="synthetic_write_failure"):
        store.recover_interrupted()
    assert store.get(first.request_id, user_id="local") == first
    assert store.get(second.request_id, user_id="local") == second
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT * FROM memory_task_inputs").fetchall() == [
            ("existing-task", "unchanged")
        ]


def test_concurrent_different_profile_never_reuses_another_scope(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    stores = [ProcessingRequestStore(database) for _ in range(2)]
    barrier = Barrier(2)

    def submit(index: int) -> ProcessingRequest | str:
        barrier.wait(timeout=5)
        try:
            return create(stores[index], profile_id=f"profile-{index}")
        except ProcessingRequestConflictError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(submit, range(2)))
    assert results.count("processing_request_scope_mismatch") == 1
    assert sum(isinstance(value, ProcessingRequest) for value in results) == 1
    assert len(stores[0].list_resumable()) == 1


def test_updated_at_is_monotonic_across_wall_clock_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = ProcessingRequestStore(tmp_path / "state.sqlite3")
    queued = create(store)

    class OldClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2000, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(requests_module, "datetime", OldClock)
    preparing = store.mark_preparing(queued)
    interrupted = store.interrupt(preparing)
    resumed = store.mark_preparing(interrupted)
    assert queued.updated_at < preparing.updated_at < interrupted.updated_at < resumed.updated_at
    with pytest.raises(ProcessingRequestConflictError):
        store.mark_prepared(preparing)


@pytest.mark.parametrize("value", [0, -1, True, "2", 1001])
def test_resumable_limit_is_validated(tmp_path: Path, value: object) -> None:
    store = ProcessingRequestStore(tmp_path / "state.sqlite3")
    with pytest.raises(ValueError):
        store.list_resumable(limit=value)  # type: ignore[arg-type]


def test_writer_lock_precedes_scope_lookup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = ProcessingRequestStore(tmp_path / "state.sqlite3")
    statements: list[str] = []
    original = requests_module.take_the_write_lock_first

    def take_lock(db: sqlite3.Connection) -> None:
        db.set_trace_callback(statements.append)
        original(db)

    monkeypatch.setattr(requests_module, "take_the_write_lock_first", take_lock)
    create(store)
    assert statements[0] == "BEGIN IMMEDIATE"
    assert statements[-1] == "COMMIT"
    assert next(index for index, sql in enumerate(statements) if sql.startswith("SELECT")) > 0
