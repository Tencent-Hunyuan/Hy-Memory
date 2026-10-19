import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_queue import FRESH_WINDOW
from one_memory.memory_tasks import (
    TASK_MAX_ATTEMPTS,
    MemoryTaskKind,
    MemoryTaskOwnershipError,
    MemoryTaskRequestConflictError,
    MemoryTaskStatus,
    MemoryTaskStore,
)
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfile,
    ModelProfileInput,
    ModelProfilePatch,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore


def activate(
    profiles: ModelProfileStore,
    profile: ModelProfile,
    secrets: InMemorySecretStore,
) -> ModelProfile:
    profiles.record_capabilities(
        profile.profile_id,
        expected_revision=profile.revision,
        capabilities=ModelCapabilities(
            endpoint_reachable=True,
            model_available=True,
            structured_output=True,
            embedding_available=True,
        ),
        secrets=secrets,
    )
    return profiles.activate(profile.profile_id, secrets)


def test_task_freezes_active_profile_revision_and_is_idempotent(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    profile = profiles.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OLLAMA,
            model="qwen",
            embedding_model="nomic-embed-text",
        ),
        secrets,
    )
    profile = activate(profiles, profile, secrets)

    first = tasks.create(
        request_id="extract:evidence-1",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=profile,
    )
    updated = profiles.update(
        profile.profile_id,
        ModelProfilePatch(expected_revision=1, model="qwen-new"),
        secrets,
    )
    duplicate = tasks.create(
        request_id="extract:evidence-1",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=profile,
    )

    assert first.task_id == duplicate.task_id
    assert first.profile_revision == 1
    assert first.profile_snapshot.model == "qwen"
    assert updated.revision == 2


def test_pending_by_recency_compares_instants_and_counts_missing_created_at_as_backlog(
    tmp_path,
) -> None:
    store, profile = _profile_and_tasks(tmp_path)
    evidence = EvidenceStore(store.database_path)
    records = [
        evidence.put(
            EvidenceInput(
                source_id="cursor",
                conversation_id="conversation",
                message_id=f"recency-{index}",
                role="user",
                written_by=WrittenBy.PERSON,
                created_at=created_at,
                verbatim=f"recency {index}",
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        ).record
        for index, created_at in enumerate(
            (datetime.now(UTC) - timedelta(hours=1), datetime.now(UTC) - timedelta(days=2), None)
        )
    ]
    fresh = store.create(
        request_id="recency-fresh",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=records[0].evidence_id,
        profile=profile,
    )
    old = store.create(
        request_id="recency-old",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=records[1].evidence_id,
        profile=profile,
    )
    missing_created = store.create(
        request_id="recency-missing-created",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=records[2].evidence_id,
        profile=profile,
    )
    now = datetime.now(UTC)

    # `created_at=None` falls back to a newly imported timestamp, so it is
    # fresh. The +08:00 offset is still the same instant as now-2h; a lexical
    # comparison would classify this differently from SQLite's julianday
    # comparison.
    fresh_since = (now - timedelta(hours=2)).astimezone(timezone(timedelta(hours=8)))
    result = store.pending_by_recency(fresh_since=fresh_since)

    assert result == (2, 1)
    assert {fresh.task_id, old.task_id, missing_created.task_id} == {
        store.get(fresh.task_id).task_id,  # type: ignore[union-attr]
        store.get(old.task_id).task_id,  # type: ignore[union-attr]
        store.get(missing_created.task_id).task_id,  # type: ignore[union-attr]
    }


def test_pending_by_recency_excludes_failed_tasks_at_the_retry_ceiling(tmp_path) -> None:
    """The queue depth must not count work the scheduler will never claim.

    ``claim()`` refuses a ``failed`` task once its attempts reach the ceiling,
    and ``active_window`` filters the same rows. Counting them as pending made
    the user-facing queue depth never fall, while no worker could ever pick
    them up. A failed task below the ceiling is still retryable and remains in
    the count.
    """
    store, profile = _profile_and_tasks(tmp_path)
    evidence = EvidenceStore(store.database_path)

    def put_evidence(message: str) -> str:
        return evidence.put(
            EvidenceInput(
                source_id="fixture",
                conversation_id="pending-recency",
                message_id=message,
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim=message,
                created_at=datetime.now(UTC),
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        ).record.evidence_id

    retryable = store.create(
        request_id="retryable",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=put_evidence("evidence-retryable"),
        profile=profile,
    )
    exhausted = store.create(
        request_id="exhausted",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=put_evidence("evidence-exhausted"),
        profile=profile,
    )
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET status=?, attempts=? WHERE task_id=?",
            (MemoryTaskStatus.FAILED, 1, retryable.task_id),
        )
        connection.execute(
            "UPDATE memory_tasks SET status=?, attempts=? WHERE task_id=?",
            (MemoryTaskStatus.FAILED, TASK_MAX_ATTEMPTS, exhausted.task_id),
        )

    assert store.pending_by_recency(fresh_since=datetime.now(UTC) - FRESH_WINDOW) == (1, 0)


def test_task_extraction_checkpoint_is_idempotent_and_recoverable(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    profile = activate(
        profiles,
        profiles.create(
            ModelProfileInput(
                name="Local",
                provider_kind=ProviderKind.OLLAMA,
                model="qwen",
                embedding_model="nomic-embed-text",
            ),
            secrets,
        ),
        secrets,
    )
    task = tasks.create(
        request_id="extract:checkpoint",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=profile,
    )

    first = tasks.save_extraction(
        task.task_id,
        content="用户偏好简洁文案。",
        layer="L2_FACT",
    )
    duplicate = tasks.save_extraction(
        task.task_id,
        content="用户偏好简洁文案。",
        layer="L2_FACT",
    )

    assert first == duplicate
    assert tasks.extraction(task.task_id) == first
    with pytest.raises(MemoryTaskRequestConflictError):
        tasks.save_extraction(
            task.task_id,
            content="用户偏好其他文案。",
            layer="L2_FACT",
        )
    _, token = tasks.claim(task.task_id)
    tasks.complete(task.task_id, runner_token=token)
    requeued = tasks.requeue_completed(
        task.task_id,
        reason="governance_projection_missing",
    )
    assert requeued.status is MemoryTaskStatus.PENDING
    assert requeued.last_error_code == "governance_projection_missing"


def test_task_waits_for_model_and_retries_with_same_snapshot(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    profile = profiles.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OLLAMA,
            model="qwen",
            embedding_model="nomic-embed-text",
        ),
        secrets,
    )
    profile = activate(profiles, profile, secrets)
    task = tasks.create(
        request_id="system2:batch-1",
        user_id="local",
        kind=MemoryTaskKind.SYSTEM2,
        input_ref="batch-1",
        profile=profile,
    )

    running, first_token = tasks.claim(task.task_id)
    waiting = tasks.wait_for_model(
        task.task_id,
        runner_token=first_token,
        error_code="model_unreachable",
    )
    resumed, second_token = tasks.claim(task.task_id)
    completed = tasks.complete(task.task_id, runner_token=second_token)

    assert running.attempts == 1
    assert waiting.status is MemoryTaskStatus.WAITING_FOR_MODEL
    assert waiting.last_error_code == "model_unreachable"
    assert resumed.attempts == 2
    assert resumed.profile_snapshot == task.profile_snapshot
    assert completed.status is MemoryTaskStatus.COMPLETED
    with pytest.raises(MemoryTaskOwnershipError):
        tasks.complete(task.task_id, runner_token=first_token)


def test_blocked_stages_name_the_stuck_stage_and_reason(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    profile = activate(
        profiles,
        profiles.create(
            ModelProfileInput(
                name="Local",
                provider_kind=ProviderKind.OLLAMA,
                model="qwen",
                embedding_model="nomic-embed-text",
            ),
            secrets,
        ),
        secrets,
    )

    for index in range(2):
        task = tasks.create(
            request_id=f"extract:blocked-{index}",
            user_id="local",
            kind=MemoryTaskKind.EXTRACT,
            input_ref=f"evidence-{index}",
            profile=profile,
        )
        _running, token = tasks.claim(task.task_id)
        tasks.wait_for_model(
            task.task_id,
            runner_token=token,
            error_code="model_unreachable",
        )
    healthy = tasks.create(
        request_id="extract:healthy",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-healthy",
        profile=profile,
    )
    _claimed, healthy_token = tasks.claim(healthy.task_id)
    tasks.complete(healthy.task_id, runner_token=healthy_token)

    blocked = tasks.blocked_stages()

    assert blocked == [(MemoryTaskKind.EXTRACT, "model_unreachable", 2)]


def test_blocked_stages_exclude_failures_at_the_retry_ceiling(tmp_path) -> None:
    """A terminal failure is not a stage still waiting to be unblocked.

    The scheduler refuses a `failed` task once its attempts reach the ceiling;
    keeping it in `blocked_stages` made the API tell the user that a stuck
    stage still needs attention while no worker could ever advance it. The
    failure remains visible in the `failed` count, but no longer as an
    actionable block.
    """
    store, profile = _profile_and_tasks(tmp_path)
    retryable = store.create(
        request_id="blocked-retryable",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-retryable",
        profile=profile,
    )
    exhausted = store.create(
        request_id="blocked-exhausted",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-exhausted",
        profile=profile,
    )
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET status=?, attempts=?, last_error_code=? WHERE task_id=?",
            (MemoryTaskStatus.FAILED, 1, "model_unreachable", retryable.task_id),
        )
        connection.execute(
            "UPDATE memory_tasks SET status=?, attempts=?, last_error_code=? WHERE task_id=?",
            (
                MemoryTaskStatus.FAILED,
                TASK_MAX_ATTEMPTS,
                "model_unreachable",
                exhausted.task_id,
            ),
        )

    assert store.blocked_stages() == [(MemoryTaskKind.EXTRACT, "model_unreachable", 1)]


def test_task_duration_estimate_ignores_time_the_app_was_closed(tmp_path) -> None:
    """The ETA must reflect how long a task takes, not how long the app was shut.

    Dividing completions by wall-clock time would blame the queue for the
    hours a desktop app spends closed, producing an ETA of days for work that
    takes an evening.
    """
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    profile = activate(
        profiles,
        profiles.create(
            ModelProfileInput(
                name="Local",
                provider_kind=ProviderKind.OLLAMA,
                model="qwen",
                embedding_model="nomic-embed-text",
            ),
            secrets,
        ),
        secrets,
    )

    task_ids = []
    for index in range(4):
        task = tasks.create(
            request_id=f"extract:rate-{index}",
            user_id="local",
            kind=MemoryTaskKind.EXTRACT,
            input_ref=f"evidence-{index}",
            profile=profile,
        )
        _claimed, token = tasks.claim(task.task_id)
        tasks.complete(task.task_id, runner_token=token)
        task_ids.append(task.task_id)

    # Each task really took 100s, but they were finished across two evenings a
    # day apart. A gap-based rate would report roughly a day per task.
    start = datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
    finished = [start, start + timedelta(seconds=100)]
    finished += [
        start + timedelta(days=1),
        start + timedelta(days=1, seconds=100),
    ]
    with sqlite3.connect(database) as connection:
        for task_id, stamp in zip(task_ids, finished, strict=True):
            connection.execute(
                """
                UPDATE memory_tasks
                SET updated_at = ?, duration_seconds = 100.0
                WHERE task_id = ?
                """,
                (stamp.isoformat(), task_id),
            )

    assert tasks.seconds_per_completion() == 100


@pytest.mark.parametrize("sample", [0, -1, True, 1.5, "10"])
def test_completion_sampling_rejects_an_invalid_sample(tmp_path, sample: object) -> None:
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    with pytest.raises(ValueError, match="sample must be a positive integer"):
        store.recent_completion_times(sample=sample)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="sample must be a positive integer"):
        store.seconds_per_completion(sample=sample)  # type: ignore[arg-type]


def test_counts_survive_a_future_task_status(tmp_path) -> None:
    """An older build must not turn a newer row into a 500 for the whole summary.

    A newer schema may persist a status this build does not know. ``counts``
    is read by the processing summary, so raising here made one unknown row
    hide every known queue count. Known statuses stay typed; the raw unknown
    value is still reported separately so it is visible rather than silently
    folded into pending or failed.
    """
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    _seed_task(store, "future-status", MemoryTaskStatus.PENDING)
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET status=? WHERE task_id=?",
            ("future_status", "future-status"),
        )

    assert store.counts() == {}
    assert store.unknown_status_counts() == {"future_status": 1}


def test_blocked_stages_survive_a_future_task_kind(tmp_path) -> None:
    """An unknown kind must not turn the blocked-stage list into a 500.

    `blocked_stages()` feeds the same processing summary as `counts()`. A
    newer build could add a task kind this build does not know; the known
    block list must still render, and the raw unknown group stays observable
    instead of being coerced into an existing kind.
    """
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    _seed_task(store, "future-kind", MemoryTaskStatus.FAILED)
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET kind=?, last_error_code=? WHERE task_id=?",
            ("future_kind", "model_unreachable", "future-kind"),
        )

    assert store.blocked_stages() == []
    assert store.unknown_kind_status_counts() == {"future_kind:failed": 1}


def test_running_task_is_recovered_after_worker_restart(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    profile = activate(
        profiles,
        profiles.create(
            ModelProfileInput(
                name="Local",
                provider_kind=ProviderKind.OLLAMA,
                model="qwen",
                embedding_model="nomic-embed-text",
            ),
            secrets,
        ),
        secrets,
    )
    task = tasks.create(
        request_id="extract:restart",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=profile,
    )
    tasks.claim(task.task_id)

    assert tasks.recover_running() == 1
    recovered = tasks.require(task.task_id)
    assert recovered.status is MemoryTaskStatus.PENDING
    assert recovered.last_error_code == "worker_restarted"
    resumed, _ = tasks.claim(task.task_id)
    assert resumed.attempts == 2


def test_superseded_profile_cancels_only_claimable_tasks(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    old_profile = activate(
        profiles,
        profiles.create(
            ModelProfileInput(
                name="Old",
                provider_kind=ProviderKind.OLLAMA,
                model="phi",
                embedding_model="nomic-embed-text",
            ),
            secrets,
        ),
        secrets,
    )
    pending = tasks.create(
        request_id="extract:pending",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-pending",
        profile=old_profile,
    )
    completed = tasks.create(
        request_id="extract:completed",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-completed",
        profile=old_profile,
    )
    _, token = tasks.claim(completed.task_id)
    tasks.complete(completed.task_id, runner_token=token)
    new_profile = activate(
        profiles,
        profiles.create(
            ModelProfileInput(
                name="New",
                provider_kind=ProviderKind.OLLAMA,
                model="qwen",
                embedding_model="nomic-embed-text",
            ),
            secrets,
        ),
        secrets,
    )

    cancelled = tasks.cancel_superseded(
        profile_id=new_profile.profile_id,
        profile_revision=new_profile.revision,
    )

    assert cancelled == 1
    assert tasks.require(pending.task_id).status is MemoryTaskStatus.CANCELLED
    assert tasks.require(completed.task_id).status is MemoryTaskStatus.COMPLETED


def test_superseded_ingest_policy_cancels_only_old_conversation_tasks(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    profile = activate(
        profiles,
        profiles.create(
            ModelProfileInput(
                name="Local",
                provider_kind=ProviderKind.OLLAMA,
                model="qwen",
                embedding_model="nomic-embed-text",
            ),
            secrets,
        ),
        secrets,
    )
    old = tasks.create(
        request_id="conversation:cursor:old-digest",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="old",
        profile=profile,
    )
    current = tasks.create(
        request_id="conversation:hy-native-v3:cursor:current-digest",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="current",
        profile=profile,
    )
    manual = tasks.create(
        request_id="manual:memory",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="manual",
        profile=profile,
    )

    cancelled = tasks.cancel_superseded_policy(active_prefix="conversation:hy-native-v3:")

    assert cancelled == 1
    assert tasks.require(old.task_id).status is MemoryTaskStatus.CANCELLED
    assert tasks.require(current.task_id).status is MemoryTaskStatus.PENDING
    assert tasks.require(manual.task_id).status is MemoryTaskStatus.PENDING


def test_task_rejects_inactive_profile_and_request_id_reuse(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    inactive = profiles.create(
        ModelProfileInput(
            name="Inactive",
            provider_kind=ProviderKind.OLLAMA,
            model="qwen",
            embedding_model="nomic-embed-text",
        ),
        secrets,
    )
    with pytest.raises(ValueError):
        tasks.create(
            request_id="extract:1",
            user_id="local",
            kind=MemoryTaskKind.EXTRACT,
            input_ref="evidence-1",
            profile=inactive,
        )

    active = activate(profiles, inactive, secrets)
    tasks.create(
        request_id="extract:1",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=active,
    )
    with pytest.raises(MemoryTaskRequestConflictError):
        tasks.create(
            request_id="extract:1",
            user_id="local",
            kind=MemoryTaskKind.EXTRACT,
            input_ref="different-evidence",
            profile=active,
        )


# ---------------------------------------------------------------------------
# Payload clearing for dead-end tasks (hy memory app: reap undrainable jobs).
#
# The important half of this is what is NOT cleared. A completed task's inputs
# are read afterwards to prove HY saw everything it was given, so clearing
# those would turn a verification into a silent pass.
# ---------------------------------------------------------------------------


def _seed_task(store: MemoryTaskStore, task_id: str, status: MemoryTaskStatus) -> None:
    now = datetime.now(UTC).isoformat()
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "INSERT INTO memory_tasks (task_id, request_id, user_id, kind, "
            "input_ref, status, profile_id, profile_revision, "
            "profile_snapshot_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                f"req:{task_id}",
                "local",
                "extract",
                "evidence-1",
                str(status),
                "profile-1",
                1,
                "{}",
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO memory_task_inputs VALUES (?,?)", (task_id, '[{"evidence_id":"e1"}]')
        )


def test_clearing_drops_payloads_of_dead_end_tasks(tmp_path) -> None:
    """Cancelled work, and failed work past its retry ceiling, are dead weight."""
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    _seed_task(store, "cancelled-1", MemoryTaskStatus.CANCELLED)
    _seed_task(store, "failed-1", MemoryTaskStatus.FAILED)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE memory_tasks SET attempts=? WHERE task_id=?",
            (3, "failed-1"),
        )

    assert store.clear_terminal_payloads() == 2
    assert store.inputs("cancelled-1") == ()
    assert store.inputs("failed-1") == ()


def test_clearing_drops_context_snapshots_too(tmp_path) -> None:
    """The reaper must clear both frozen-input tables, not only `inputs`.

    `clear_terminal_payloads` deletes from `memory_task_inputs` and
    `memory_task_contexts` in one bounded pass. Earlier tests only asserted
    the first table, so deleting the second would still pass while a dead-end
    task kept its context snapshot forever.
    """
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    _seed_task(store, "cancelled-context", MemoryTaskStatus.CANCELLED)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "INSERT INTO memory_task_contexts VALUES (?,?)",
            ("cancelled-context", '[{"evidence_id":"context-e1"}]'),
        )

    assert store.clear_terminal_payloads() == 1
    assert store.inputs("cancelled-context") == ()
    assert store.context_inputs("cancelled-context") == ()
    with sqlite3.connect(store.database_path) as connection:
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM memory_task_contexts WHERE task_id=?",
                ("cancelled-context",),
            ).fetchone()[0]
            == 0
        )


def test_context_snapshots_survive_for_a_retryable_failed_task(tmp_path) -> None:
    """Reaping is transactional by task, not by table."""
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    _seed_task(store, "retryable-context", MemoryTaskStatus.FAILED)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "INSERT INTO memory_task_contexts VALUES (?,?)",
            ("retryable-context", '[{"evidence_id":"context-e1"}]'),
        )

    assert store.clear_terminal_payloads() == 0
    assert store.inputs("retryable-context") == ({"evidence_id": "e1"},)
    assert store.context_inputs("retryable-context") == ({"evidence_id": "context-e1"},)


def test_clearing_finds_contexts_when_inputs_were_already_removed(tmp_path) -> None:
    """A partial prior cleanup must not strand the remaining context row.

    The first version selected candidates through an inner JOIN on
    `memory_task_inputs`. If inputs had already been deleted while contexts
    remained (old version, interrupted cleanup, restore), no later pass could
    ever see that task, and its context snapshot would live forever.
    """
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    _seed_task(store, "context-only", MemoryTaskStatus.CANCELLED)
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute("DELETE FROM memory_task_inputs WHERE task_id=?", ("context-only",))
        connection.execute(
            "INSERT INTO memory_task_contexts VALUES (?,?)",
            ("context-only", '[{"evidence_id":"context-only"}]'),
        )

    assert store.context_inputs("context-only") == ({"evidence_id": "context-only"},)
    assert store.clear_terminal_payloads() == 1
    assert store.context_inputs("context-only") == ()
    # A second pass is a clean no-op, not an endless report of work done.
    assert store.clear_terminal_payloads() == 0


def test_clearing_keeps_payloads_of_a_retryable_failed_task(tmp_path) -> None:
    """A failed task below the retry ceiling still needs its frozen input.

    `claim()` accepts `FAILED` and re-runs the same task up to
    `TASK_MAX_ATTEMPTS`; `process_records` reads `tasks.inputs()` first and
    only falls back to rebuilding from Evidence when the snapshot is gone.
    Clearing a retryable failed task's inputs therefore changes what the retry
    sends — from the frozen snapshot to a freshly rebuilt chunk. The snapshot
    may be reaped only once the task is genuinely terminal (cancelled, or
    failed at the attempt ceiling); a retryable one must keep it.
    """
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    _seed_task(store, "retryable-1", MemoryTaskStatus.FAILED)

    assert store.clear_terminal_payloads() == 0
    assert store.inputs("retryable-1") == ({"evidence_id": "e1"},)


def test_clearing_drops_payloads_of_a_failed_task_at_the_retry_ceiling(tmp_path) -> None:
    """Once retries are exhausted the snapshot really is dead weight."""
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    _seed_task(store, "exhausted-1", MemoryTaskStatus.FAILED)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE memory_tasks SET attempts=? WHERE task_id=?",
            (3, "exhausted-1"),
        )

    assert store.clear_terminal_payloads() == 1
    assert store.inputs("exhausted-1") == ()


def test_clearing_never_touches_completed_tasks(tmp_path) -> None:
    """Completed inputs are evidence of what HY was actually given.

    `memory_processing` compares them against the records sent to HY to prove
    coverage, and `matters/discovery` reads them to report how many records HY
    saw. Clearing them would make a failed coverage check look like a pass.
    """
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    _seed_task(store, "done-1", MemoryTaskStatus.COMPLETED)

    assert store.clear_terminal_payloads() == 0
    assert store.inputs("done-1") == ({"evidence_id": "e1"},)


def test_clearing_never_touches_unknown_tasks(tmp_path) -> None:
    """Unknown input identity is retained for later outcome reconciliation."""
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    _seed_task(store, "unknown-1", MemoryTaskStatus.UNKNOWN)

    assert store.clear_terminal_payloads() == 0
    assert store.inputs("unknown-1") == ({"evidence_id": "e1"},)


def test_promote_deferred_orders_iso_times_as_instants(tmp_path) -> None:
    """Manual promotion must promote the newest evidence by instant."""
    store, profile = _profile_and_tasks(tmp_path)
    evidence = EvidenceStore(store.database_path)
    records = [
        evidence.put(
            EvidenceInput(
                source_id="cursor",
                conversation_id="conversation",
                message_id=f"promote-{index}",
                role="user",
                written_by=WrittenBy.PERSON,
                created_at=datetime.now(UTC),
                verbatim=f"promote {index}",
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        ).record
        for index in range(2)
    ]
    earlier = store.create(
        request_id="promote-earlier",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=records[0].evidence_id,
        profile=profile,
    )
    later = store.create(
        request_id="promote-later",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=records[1].evidence_id,
        profile=profile,
    )
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE evidence SET created_at=? WHERE evidence_id=?",
            ("2026-09-09T08:00:00+08:00", records[0].evidence_id),
        )
        connection.execute(
            "UPDATE evidence SET created_at=? WHERE evidence_id=?",
            ("2026-09-09T01:00:00+00:00", records[1].evidence_id),
        )
        connection.execute(
            "UPDATE memory_tasks SET status=? WHERE task_id IN (?,?)",
            (
                str(MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW),
                earlier.task_id,
                later.task_id,
            ),
        )

    promoted = store.promote_deferred(limit=1)

    assert [task.task_id for task in promoted] == [later.task_id]


def test_promote_deferred_does_not_treat_recent_import_as_conversation_time(tmp_path) -> None:
    store, profile = _profile_and_tasks(tmp_path)
    evidence = EvidenceStore(store.database_path)
    known = evidence.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="known",
            message_id="known",
            role="user",
            written_by=WrittenBy.PERSON,
            created_at=datetime(2026, 9, 9, tzinfo=UTC),
            verbatim="known conversation time",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    unknown = evidence.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="unknown",
            message_id="unknown",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="unknown conversation time",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    known_task = store.create(
        request_id="known-time",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=known.evidence_id,
        profile=profile,
    )
    unknown_task = store.create(
        request_id="unknown-time",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=unknown.evidence_id,
        profile=profile,
    )
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET status=? WHERE task_id IN (?,?)",
            (
                str(MemoryTaskStatus.DEFERRED_OUTSIDE_WINDOW),
                known_task.task_id,
                unknown_task.task_id,
            ),
        )

    promoted = store.promote_deferred(limit=1)

    assert [task.task_id for task in promoted] == [known_task.task_id]


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "10"])
def test_promote_deferred_rejects_an_invalid_limit(tmp_path, limit: object) -> None:
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    with pytest.raises(ValueError, match="limit must be a positive integer"):
        store.promote_deferred(limit=limit)  # type: ignore[arg-type]


def test_unknown_task_can_only_be_resolved_by_an_inquiry(tmp_path) -> None:
    """Unknown must leave only through found/not-found, never a blind retry."""
    store, profile = _profile_and_tasks(tmp_path)
    unknown_found = store.create(
        request_id="unknown-found",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-found",
        profile=profile,
    )
    unknown_missing = store.create(
        request_id="unknown-missing",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-missing",
        profile=profile,
    )
    not_unknown = store.create(
        request_id="not-unknown",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-failed",
        profile=profile,
    )
    for task_id, status in (
        (unknown_found.task_id, MemoryTaskStatus.UNKNOWN),
        (unknown_missing.task_id, MemoryTaskStatus.UNKNOWN),
        (not_unknown.task_id, MemoryTaskStatus.FAILED),
    ):
        with sqlite3.connect(store.database_path) as connection:
            connection.execute(
                "UPDATE memory_tasks SET status=? WHERE task_id=?",
                (str(status), task_id),
            )

    assert (
        store.resolve_unknown(unknown_found.task_id, found=True, detail="found in HY")
        is MemoryTaskStatus.COMPLETED
    )
    assert (
        store.resolve_unknown(unknown_missing.task_id, found=False, detail="not in HY")
        is MemoryTaskStatus.FAILED
    )
    assert store.resolve_unknown(not_unknown.task_id, found=True) is None
    assert store.resolve_unknown(unknown_found.task_id, found=False, detail="stale report") is None

    assert store.require(unknown_found.task_id).status is MemoryTaskStatus.COMPLETED
    assert store.require(unknown_found.task_id).last_error_code == "found in HY"
    assert store.require(unknown_missing.task_id).status is MemoryTaskStatus.FAILED
    assert store.require(unknown_missing.task_id).last_error_code == "not in HY"
    assert store.require(not_unknown.task_id).status is MemoryTaskStatus.FAILED
    with pytest.raises(MemoryTaskOwnershipError, match="retry budget"):
        store.claim(unknown_missing.task_id)

    # Without an explicit detail, a not-found resolution still needs a stable
    # code so failure metrics can tell this apart from a pipeline failure.
    no_detail = store.create(
        request_id="unknown-no-detail",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-no-detail",
        profile=profile,
    )
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE memory_tasks SET status=? WHERE task_id=?",
            (str(MemoryTaskStatus.UNKNOWN), no_detail.task_id),
        )
    assert store.resolve_unknown(no_detail.task_id, found=False) is MemoryTaskStatus.FAILED
    assert store.require(no_detail.task_id).last_error_code == "model_content_outcome_not_found"


def test_confirmed_retry_requeues_task_only_unknown_without_a_durable_job(tmp_path) -> None:
    store, profile = _profile_and_tasks(tmp_path)
    task = store.create(
        request_id="orphan-unknown",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="orphan-evidence",
        profile=profile,
    )
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET status=?, attempts=3, last_error_code=? WHERE task_id=?",
            (
                MemoryTaskStatus.UNKNOWN,
                "model_content_outcome_unknown",
                task.task_id,
            ),
        )

    assert store.retry_unknown_confirmed() == 1
    retried = store.require(task.task_id)
    assert retried.status is MemoryTaskStatus.PENDING
    assert retried.attempts == 0
    assert retried.last_error_code == "model_content_manual_retry_requested"
    assert store.retry_unknown_confirmed() == 0


def test_task_only_unknown_is_automatically_retried_only_once(tmp_path) -> None:
    store, profile = _profile_and_tasks(tmp_path)
    task = store.create(
        request_id="auto-retry-unknown",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="orphan-evidence",
        profile=profile,
    )
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET status=?,attempts=1,last_error_code=?,updated_at=? "
            "WHERE task_id=?",
            (
                MemoryTaskStatus.UNKNOWN,
                "model_content_outcome_unknown",
                "2026-01-01T00:00:00+00:00",
                task.task_id,
            ),
        )

    cutoff = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    assert store.retry_first_unknown_after_reconcile(older_than=cutoff) == 1
    retried = store.require(task.task_id)
    assert retried.status is MemoryTaskStatus.PENDING
    assert retried.attempts == 1

    running, token = store.claim(task.task_id)
    assert running.attempts == 2
    store.mark_unknown(
        task.task_id,
        runner_token=token,
        error_code="model_content_outcome_unknown",
    )
    assert (
        store.retry_first_unknown_after_reconcile(
            older_than=datetime.now(UTC) + timedelta(minutes=1)
        )
        == 0
    )
    assert store.require(task.task_id).status is MemoryTaskStatus.UNKNOWN


def test_authority_invalid_task_is_never_reinvoked(tmp_path) -> None:
    store, profile = _profile_and_tasks(tmp_path)
    task = store.create(
        request_id="authority-invalid",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="authority-evidence",
        profile=profile,
    )
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET status=?,attempts=1,last_error_code=?,updated_at=? "
            "WHERE task_id=?",
            (
                MemoryTaskStatus.UNKNOWN,
                "hy_memory_write_verification_authority_invalid",
                "2026-01-01T00:00:00+00:00",
                task.task_id,
            ),
        )

    cutoff = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    assert store.retry_first_unknown_after_reconcile(older_than=cutoff) == 0
    assert store.retry_unknown_confirmed() == 0
    retained = store.require(task.task_id)
    assert retained.status is MemoryTaskStatus.UNKNOWN
    assert retained.attempts == 1
    assert retained.last_error_code == "hy_memory_write_verification_authority_invalid"


def test_linked_unknown_is_retried_only_by_the_durable_job(tmp_path) -> None:
    from one_memory.memory_server.contracts import BatchIdentity, WriteRequest
    from one_memory.memory_server.durable_queue import DurableWriteQueue

    store, profile = _profile_and_tasks(tmp_path)
    evidence = EvidenceStore(store.database_path)
    record = evidence.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="linked-conversation",
            message_id="linked-message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="linked unknown",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    task = store.create(
        request_id="linked-task",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=record.evidence_id,
        profile=profile,
        input_records=(record,),
    )
    queue = DurableWriteQueue(store.database_path, evidence=evidence)
    job = queue.submit(
        WriteRequest(
            internal_session_id="linked-session",
            batch_identity=BatchIdentity(
                internal_session_id="linked-session",
                first_round=1,
                last_round=1,
            ),
            evidence_ids=[record.evidence_id],
            idempotency_key="linked-job",
            source_id="cursor",
        )
    )
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET status=?,attempts=1,last_error_code=?,updated_at=? "
            "WHERE task_id=?",
            (
                MemoryTaskStatus.UNKNOWN,
                "model_content_outcome_unknown",
                "2026-01-01T00:00:00+00:00",
                task.task_id,
            ),
        )
        connection.execute(
            "UPDATE memory_write_jobs SET state='unknown',attempts=1,hy_invoked=1,"
            "last_error='model_content_outcome_unknown',updated_at=? WHERE job_id=?",
            ("2026-01-01T00:00:00+00:00", job.job_id),
        )

    cutoff = datetime(2026, 1, 1, 0, 5, tzinfo=UTC)
    assert store.retry_first_unknown_after_reconcile(older_than=cutoff) == 0
    assert store.require(task.task_id).status is MemoryTaskStatus.UNKNOWN
    assert queue.retry_first_unknown_after_reconcile(older_than=cutoff) == 1
    assert store.require(task.task_id).status is MemoryTaskStatus.PENDING
    assert queue.get(job.request_id).state == "pending"
    assert (
        store.active_window(
            now=datetime(2026, 1, 2, tzinfo=UTC),
            fresh_window=timedelta(days=1),
            extraction_window=None,
        )
        == []
    )


def test_orphan_recovery_preserves_active_running_tasks(tmp_path) -> None:
    store, profile = _profile_and_tasks(tmp_path)
    active = store.create(
        request_id="active-running",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="active-evidence",
        profile=profile,
    )
    orphan = store.create(
        request_id="orphan-running",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="orphan-evidence",
        profile=profile,
    )
    store.claim(active.task_id)
    store.claim(orphan.task_id)

    assert store.recover_orphan_running(exclude_task_ids=(active.task_id,)) == 1
    assert store.require(active.task_id).status is MemoryTaskStatus.RUNNING
    assert store.require(orphan.task_id).status is MemoryTaskStatus.PENDING


def test_orphan_recovery_never_reclaims_a_task_claimed_after_its_snapshot(tmp_path) -> None:
    store, profile = _profile_and_tasks(tmp_path)
    old = store.create(
        request_id="old-running",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="old-evidence",
        profile=profile,
    )
    fresh = store.create(
        request_id="fresh-running",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="fresh-evidence",
        profile=profile,
    )
    store.claim(old.task_id)
    cutoff = datetime.now(UTC)
    store.claim(fresh.task_id)

    assert store.recover_orphan_running(claimed_before=cutoff) == 1
    assert store.require(old.task_id).status is MemoryTaskStatus.PENDING
    assert store.require(fresh.task_id).status is MemoryTaskStatus.RUNNING


def test_unknown_tasks_lists_only_unknown_rows_with_a_stable_cursor(tmp_path) -> None:
    """A reconcile pass must discover task unknown rows without guessing SQL.

    The durable queue has ``unknown_jobs`` for its half, but the task layer
    only exposed ``resolve_unknown``. A composition root therefore had to
    write its own query, and a query that accidentally included ``running``
    or ``failed`` would either re-send work or bury a possibly-delivered
    write. This primitive is read-only and ordered by ``(updated_at, task_id)``
    so paging is stable.
    """
    store, profile = _profile_and_tasks(tmp_path)
    unknown_ids = []
    for index in range(3):
        task = store.create(
            request_id=f"unknown-list-{index}",
            user_id="local",
            kind=MemoryTaskKind.EXTRACT,
            input_ref=f"evidence-{index}",
            profile=profile,
        )
        with sqlite3.connect(store.database_path) as connection, connection:
            connection.execute(
                "UPDATE memory_tasks SET status=?, updated_at=? WHERE task_id=?",
                (
                    MemoryTaskStatus.UNKNOWN,
                    f"2026-01-01T00:00:0{index}+00:00",
                    task.task_id,
                ),
            )
        unknown_ids.append(task.task_id)
    store.create(
        request_id="not-unknown-list",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-pending",
        profile=profile,
    )

    first = store.unknown_tasks(limit=2)
    second = store.unknown_tasks(
        limit=2,
        after_cursor=(first[-1].updated_at.isoformat(), first[-1].task_id),
    )

    assert [item.task_id for item in (*first, *second)] == unknown_ids
    assert all(item.status is MemoryTaskStatus.UNKNOWN for item in (*first, *second))
    assert all(store.get(item.task_id).status is MemoryTaskStatus.UNKNOWN for item in first)


def test_unknown_tasks_tolerates_a_row_resolved_between_pages(tmp_path) -> None:
    """Discovery is not a snapshot; a concurrently resolved row disappears."""
    store, profile = _profile_and_tasks(tmp_path)
    task = store.create(
        request_id="unknown-page",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-page",
        profile=profile,
    )
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET status=?, updated_at=? WHERE task_id=?",
            (
                MemoryTaskStatus.UNKNOWN,
                "2026-01-01T00:00:00+00:00",
                task.task_id,
            ),
        )

    first = store.unknown_tasks(limit=1)
    assert [item.task_id for item in first] == [task.task_id]
    assert store.resolve_unknown(task.task_id, found=True) is MemoryTaskStatus.COMPLETED

    rest = store.unknown_tasks(
        limit=10,
        after_cursor=(first[-1].updated_at.isoformat(), first[-1].task_id),
    )
    assert rest == ()
    assert store.get(task.task_id).status is MemoryTaskStatus.COMPLETED


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "10"])
def test_unknown_tasks_rejects_an_invalid_limit(tmp_path, limit: object) -> None:
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    with pytest.raises(ValueError, match="limit must be positive"):
        store.unknown_tasks(limit=limit)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "cursor",
    [
        ("only-one",),
        ("a", "b", "c"),
        ("2026-01-01T00:00:00+00:00", None),
        (None, "task"),
        ("", "task"),
    ],
)
def test_unknown_tasks_rejects_an_invalid_cursor(tmp_path, cursor: object) -> None:
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    with pytest.raises(ValueError, match="after_cursor"):
        store.unknown_tasks(after_cursor=cursor)  # type: ignore[arg-type]


def test_the_shipped_schema_serves_the_unknown_task_order(tmp_path) -> None:
    """The real schema must not materialise+sort every unknown task.

    `unknown_tasks` orders by `(updated_at, task_id)`; the only status index is
    `memory_tasks_status(status, created_at)`, so without the reconcile-order
    index the plan is `... USING INDEX memory_tasks_status | USE TEMP B-TREE
    FOR ORDER BY`. This pins the shipped DDL, not a test-local index.
    """
    import asyncio

    from one_memory.database_access import main_database
    from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
    from one_memory.memory_server.migrations import MIGRATIONS

    path = tmp_path / "one-memory.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(path), MIGRATIONS))

    with main_database(path).read() as connection:
        plan = [
            str(row[3])
            for row in connection.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM memory_tasks WHERE status=? "
                "ORDER BY updated_at, task_id LIMIT ?",
                ("unknown", 128),
            )
        ]

    joined = " | ".join(plan)
    assert "idx_memory_tasks_unknown" in joined, joined
    assert "USE TEMP B-TREE FOR ORDER BY" not in joined, joined


def test_unknown_error_counts_keep_the_real_outcome_codes_visible(tmp_path) -> None:
    """A backlog must not collapse every unknown into one anonymous bucket."""
    store, profile = _profile_and_tasks(tmp_path)
    unknown = {}
    for name in ("unknown-auth", "unknown-model", "unknown-legacy"):
        task = store.create(
            request_id=name,
            user_id="local",
            kind=MemoryTaskKind.EXTRACT,
            input_ref=f"evidence-{name}",
            profile=profile,
        )
        unknown[name] = task.task_id
    failed = store.create(
        request_id="failed-control",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-failed",
        profile=profile,
    )
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET status=?, last_error_code=? WHERE task_id=?",
            (
                MemoryTaskStatus.UNKNOWN,
                "model_content_permission_revoked",
                unknown["unknown-auth"],
            ),
        )
        connection.execute(
            "UPDATE memory_tasks SET status=?, last_error_code=? WHERE task_id=?",
            (
                MemoryTaskStatus.UNKNOWN,
                "hy_memory_model_rate_limited",
                unknown["unknown-model"],
            ),
        )
        connection.execute(
            "UPDATE memory_tasks SET status=?, last_error_code=? WHERE task_id=?",
            (
                MemoryTaskStatus.UNKNOWN,
                "model_content_outcome_unknown",
                unknown["unknown-legacy"],
            ),
        )
        connection.execute(
            "UPDATE memory_tasks SET last_error_code='worker_restarted' WHERE task_id=?",
            (failed.task_id,),
        )

    assert store.unknown_error_counts() == {
        "hy_memory_model_rate_limited": 1,
        "model_content_outcome_unknown": 1,
        "model_content_permission_revoked": 1,
    }


def test_permission_wait_is_distinct_and_can_be_reclaimed(tmp_path) -> None:
    store, profile = _profile_and_tasks(tmp_path)
    task = store.create(
        request_id="permission-wait",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-permission",
        profile=profile,
    )
    claimed, token = store.claim(task.task_id)
    assert claimed.status is MemoryTaskStatus.RUNNING

    waiting = store.wait_for_permission(
        task.task_id,
        runner_token=token,
        error_code="model_content_permission_required",
    )
    assert waiting.status is MemoryTaskStatus.WAITING_FOR_PERMISSION
    assert store.counts()[MemoryTaskStatus.WAITING_FOR_PERMISSION] == 1

    resumed, _ = store.claim(task.task_id)
    assert resumed.status is MemoryTaskStatus.RUNNING


def test_unstarted_permission_wait_keeps_its_payload(tmp_path) -> None:
    store, profile = _profile_and_tasks(tmp_path)
    task = store.create(
        request_id="permission-park",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-permission",
        profile=profile,
    )
    assert store.park_for_permission(task.task_id, error_code="model_content_permission_required")
    assert store.require(task.task_id).status is MemoryTaskStatus.WAITING_FOR_PERMISSION
    assert store.clear_terminal_payloads() == 0


def test_legacy_unknown_error_is_migrated_to_unknown_status(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    store = MemoryTaskStore(database)
    _seed_task(store, "legacy-unknown", MemoryTaskStatus.FAILED)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE memory_tasks SET last_error_code=? WHERE task_id=?",
            ("model_content_outcome_unknown", "legacy-unknown"),
        )

    reopened = MemoryTaskStore(database)

    with sqlite3.connect(reopened.database_path) as connection:
        status = connection.execute(
            "SELECT status FROM memory_tasks WHERE task_id=?", ("legacy-unknown",)
        ).fetchone()[0]
    assert status == MemoryTaskStatus.UNKNOWN


def test_timeout_before_hy_invocation_is_retryable(tmp_path) -> None:
    store, profile = _profile_and_tasks(tmp_path)
    task = store.create(
        request_id="pre-invocation-timeout",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-timeout",
        profile=profile,
    )
    claimed, _token = store.claim(task.task_id)
    assert claimed.status is MemoryTaskStatus.RUNNING

    assert store.recover_one_running(task.task_id, reason="hy_memory_timeout")
    recovered = store.require(task.task_id)
    assert recovered.status is MemoryTaskStatus.PENDING
    assert recovered.last_error_code == "hy_memory_timeout"


def test_timeout_after_hy_invocation_stays_unknown(tmp_path) -> None:
    store, profile = _profile_and_tasks(tmp_path)
    task = store.create(
        request_id="post-invocation-timeout",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-timeout",
        profile=profile,
    )
    _claimed, token = store.claim(task.task_id)
    store.record_hy_invocation(task.task_id, runner_token=token)

    assert store.recover_one_running(task.task_id, reason="hy_memory_timeout")
    recovered = store.require(task.task_id)
    assert recovered.status is MemoryTaskStatus.UNKNOWN
    assert recovered.last_error_code == "model_content_outcome_unknown"


def test_legacy_unknown_status_migration_is_bounded_and_resumable(tmp_path) -> None:
    """Opening a large legacy database must not update every row at once.

    Older builds stored the unknown outcome as `failed` plus a sentinel error
    code. A first-run corpus can hold hundreds of thousands of those rows, and
    an unbounded UPDATE on the single writer is exactly the long write the
    short-transaction budget forbids. The migration is bounded per call and
    resumes until no legacy row remains.
    """
    database = tmp_path / "one-memory.sqlite3"
    store = MemoryTaskStore(database)
    for index in range(3):
        _seed_task(store, f"legacy-{index}", MemoryTaskStatus.FAILED)
    with sqlite3.connect(database) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET last_error_code='model_content_outcome_unknown'"
        )

    assert store.migrate_legacy_unknown_statuses(limit=2) == 2
    with sqlite3.connect(database) as connection:
        remaining = connection.execute(
            "SELECT COUNT(*) FROM memory_tasks WHERE status=?",
            (MemoryTaskStatus.FAILED,),
        ).fetchone()[0]
    assert remaining == 1

    assert store.migrate_legacy_unknown_statuses(limit=2) == 1
    assert store.migrate_legacy_unknown_statuses(limit=2) == 0


def test_restart_splits_a_sent_continuous_task_from_an_unsent_one(tmp_path) -> None:
    """One restart, two verdicts: unknown for possibly-sent, pending for not.

    The dangerous case is a crashed worker whose HY call may have landed:
    returning it to `pending` would send it again, and HY has no idempotency
    key. An ordinary running task never reached HY, so it may retry.
    """
    store, profile = _profile_and_tasks(tmp_path)
    sent = store.create(
        request_id="continuous-sent",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-sent",
        profile=profile,
    )
    unsent = store.create(
        request_id="ordinary-unsent",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-unsent",
        profile=profile,
    )
    _sent_task, sent_token = store.claim(sent.task_id)
    store.record_hy_invocation(sent.task_id, runner_token=sent_token)
    store.claim(unsent.task_id)

    recovered = store.recover_running()

    assert recovered == 2
    settled = store.require(sent.task_id)
    assert settled.status is MemoryTaskStatus.UNKNOWN
    assert settled.last_error_code == "model_content_outcome_unknown"
    assert store.require(unsent.task_id).status is MemoryTaskStatus.PENDING
    # Unknown must not decay back into a retryable pending row.
    with pytest.raises(MemoryTaskOwnershipError):
        store.claim(sent.task_id)


def test_clearing_is_bounded(tmp_path) -> None:
    """A large backlog must not be cleared in one long write."""
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    for index in range(5):
        _seed_task(store, f"cancelled-{index}", MemoryTaskStatus.CANCELLED)

    assert store.clear_terminal_payloads(limit=2) == 2
    assert store.clear_terminal_payloads(limit=2) == 2
    assert store.clear_terminal_payloads(limit=2) == 1
    assert store.clear_terminal_payloads(limit=2) == 0


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "10"])
def test_clearing_rejects_a_non_positive_limit(tmp_path, limit: object) -> None:
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    with pytest.raises(ValueError):
        store.clear_terminal_payloads(limit=limit)  # type: ignore[arg-type]


def test_clearing_keeps_oversized_preparation_failures(tmp_path) -> None:
    """An oversized failure's snapshot is the finding, not dead weight.

    The oversized path records which records did not fit a model input, and
    groups later arrivals against that snapshot by comparing refs. Reaping it
    would leave a bare error code that cannot be acted on — the same class of
    silent-failure the completed-payload rule already guards against.
    """
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    store.clear_terminal_payloads()
    _seed_task(store, "oversized-1", MemoryTaskStatus.FAILED)
    with sqlite3.connect(store.database_path) as connection:
        connection.execute(
            "UPDATE memory_tasks SET request_id=? WHERE task_id=?",
            ("oversized:" + "0" * 64 + ":req", "oversized-1"),
        )

    assert store.clear_terminal_payloads() == 0
    assert store.inputs("oversized-1") == ({"evidence_id": "e1"},)


def _profile_and_tasks(tmp_path) -> tuple[MemoryTaskStore, ModelProfile]:
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    profile = activate(
        profiles,
        profiles.create(
            ModelProfileInput(
                name="Local",
                provider_kind=ProviderKind.OLLAMA,
                model="qwen",
                embedding_model="nomic-embed-text",
            ),
            secrets,
        ),
        secrets,
    )
    return tasks, profile


def _completed_conversation_task(
    *,
    tasks: MemoryTaskStore,
    evidence: EvidenceStore,
    profile: ModelProfile,
    request_id: str,
    conversation_id: str,
    workspace: str | None,
) -> tuple[object, object]:
    record = evidence.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id=conversation_id,
            workspace=workspace,
            message_id=request_id,
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim=f"{request_id} {workspace}",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    task = tasks.create(
        request_id=request_id,
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=record.evidence_id,
        profile=profile,
        input_records=(record,),
    )
    _claimed, token = tasks.claim(task.task_id)
    tasks.complete(task.task_id, runner_token=token)
    return task, record


def _pending_conversation_seed(
    *,
    tasks: MemoryTaskStore,
    evidence: EvidenceStore,
    profile: ModelProfile,
    request_id: str,
    conversation_id: str,
    workspace: str | None,
):
    record = evidence.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id=conversation_id,
            workspace=workspace,
            message_id=request_id,
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim=f"{request_id} {workspace}",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    task = tasks.create(
        request_id=request_id,
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=record.evidence_id,
        profile=profile,
        input_records=(record,),
    )
    return task, record


def test_completed_history_keeps_same_named_conversations_in_different_workspaces_apart(
    tmp_path,
) -> None:
    """A resume in project A must not adopt project B's completed history.

    Cursor can reuse a conversation id across projects. The task row has no
    workspace column, so the truthful scope is the seed Evidence row joined by
    the same workspace identity. A missing workspace is its own value, not a
    wildcard.
    """
    tasks, profile = _profile_and_tasks(tmp_path)
    evidence = EvidenceStore(tasks.database_path)
    first_old, _ = _completed_conversation_task(
        tasks=tasks,
        evidence=evidence,
        profile=profile,
        request_id="conversation:hy-native-v3:workspace-a-old",
        conversation_id="same-conversation",
        workspace="workspace:v1:project-a",
    )
    second_old, _ = _completed_conversation_task(
        tasks=tasks,
        evidence=evidence,
        profile=profile,
        request_id="conversation:hy-native-v3:workspace-b-old",
        conversation_id="same-conversation",
        workspace="workspace:v1:project-b",
    )
    first_seed_task, first_seed = _pending_conversation_seed(
        tasks=tasks,
        evidence=evidence,
        profile=profile,
        request_id="conversation:hy-native-v3:workspace-a-current",
        conversation_id="same-conversation",
        workspace="workspace:v1:project-a",
    )
    second_seed_task, second_seed = _pending_conversation_seed(
        tasks=tasks,
        evidence=evidence,
        profile=profile,
        request_id="conversation:hy-native-v3:workspace-b-current",
        conversation_id="same-conversation",
        workspace="workspace:v1:project-b",
    )

    # Positive control: the storage/read path already sees two conversations
    # because workspace is part of the seed identity.
    assert len(evidence.list_conversation_seed_ids()) == 2

    first_tasks = tasks.completed_conversation_tasks(first_seed_task, first_seed)
    second_tasks = tasks.completed_conversation_tasks(second_seed_task, second_seed)
    assert {task.task_id for task in first_tasks} == {first_old.task_id}
    assert {task.task_id for task in second_tasks} == {second_old.task_id}

    prefix = "conversation:hy-native-v3:"
    first_inputs = tasks.completed_inputs_before(
        first_seed,
        active_prefix=prefix,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
    )
    second_inputs = tasks.completed_inputs_before(
        second_seed,
        active_prefix=prefix,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
    )
    # The current seed may or may not appear depending on the caller's
    # completed-policy boundary; the invariant here is that neither call may
    # return the other workspace's Evidence.
    assert first_seed.evidence_id not in {ref["evidence_id"] for ref in second_inputs}
    assert second_seed.evidence_id not in {ref["evidence_id"] for ref in first_inputs}


def test_completed_history_treats_null_workspace_as_an_identity_not_a_wildcard(
    tmp_path,
) -> None:
    tasks, profile = _profile_and_tasks(tmp_path)
    evidence = EvidenceStore(tasks.database_path)
    null_completed, null_completed_seed = _completed_conversation_task(
        tasks=tasks,
        evidence=evidence,
        profile=profile,
        request_id="conversation:hy-native-v3:null-old",
        conversation_id="same-conversation",
        workspace=None,
    )
    scoped_completed, scoped_completed_seed = _completed_conversation_task(
        tasks=tasks,
        evidence=evidence,
        profile=profile,
        request_id="conversation:hy-native-v3:scoped-old",
        conversation_id="same-conversation",
        workspace="workspace:v1:project",
    )
    with sqlite3.connect(tasks.database_path) as connection, connection:
        connection.execute(
            "UPDATE evidence SET workspace=NULL WHERE evidence_id=?",
            (null_completed_seed.evidence_id,),
        )
        connection.execute(
            "UPDATE evidence SET workspace='workspace:v1:project' WHERE evidence_id=?",
            (scoped_completed_seed.evidence_id,),
        )
    null_seed = evidence.get(null_completed_seed.evidence_id)
    scoped_seed = evidence.get(scoped_completed_seed.evidence_id)
    assert null_seed is not None and null_seed.workspace is None
    assert scoped_seed is not None and scoped_seed.workspace == "workspace:v1:project"

    null_candidate = tasks.create(
        request_id="conversation:hy-native-v3:null-current",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=null_seed.evidence_id,
        profile=profile,
    )
    scoped_candidate = tasks.create(
        request_id="conversation:hy-native-v3:scoped-current",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=scoped_seed.evidence_id,
        profile=profile,
    )
    null_history = {
        task.task_id for task in tasks.completed_conversation_tasks(null_candidate, null_seed)
    }
    scoped_history = {
        task.task_id for task in tasks.completed_conversation_tasks(scoped_candidate, scoped_seed)
    }
    assert null_history == {null_completed.task_id}
    assert scoped_history == {scoped_completed.task_id}


def test_a_stopped_policy_cancels_its_queued_tasks(tmp_path) -> None:
    """Tasks refused because the user turned the policy off are not failures.

    Nothing was ever sent to HY, so `failed` reads as a broken pipeline. They
    are cancelled with `policy_stopped_by_user` instead, and still reported as
    their own number so the two never get conflated.
    """
    store, profile = _profile_and_tasks(tmp_path)
    codes = (
        "model_content_policy_not_current_or_enabled",
        "model_content_permission_revoked",
    )
    stopped_ids = []
    for index, code in enumerate(codes):
        task = store.create(
            request_id=f"conversation:stopped-{index}",
            user_id="local",
            kind=MemoryTaskKind.EXTRACT,
            input_ref=f"evidence-{index}",
            profile=profile,
        )
        _claimed, token = store.claim(task.task_id)
        store.fail(task.task_id, runner_token=token, error_code=code)
        stopped_ids.append(task.task_id)

    assert store.reclassify_policy_stopped() == 2

    for task_id, code in zip(stopped_ids, codes, strict=True):
        task = store.require(task_id)
        assert task.status is MemoryTaskStatus.CANCELLED
        # The original code is kept: it says which permission state refused.
        assert task.last_error_code == code
    assert store.unexecuted_policy_failures() == 2


def test_reclassifying_leaves_real_failures_alone(tmp_path) -> None:
    """Otherwise a stopped policy would hide genuine breakage."""
    store, profile = _profile_and_tasks(tmp_path)
    task = store.create(
        request_id="conversation:real-1",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-real",
        profile=profile,
    )
    _claimed, token = store.claim(task.task_id)
    store.fail(task.task_id, runner_token=token, error_code="hy_memory_runtime_failed")

    assert store.reclassify_policy_stopped() == 0
    assert store.require(task.task_id).status is MemoryTaskStatus.FAILED
    assert store.unexecuted_policy_failures() == 0


def test_stopped_tasks_are_counted_whether_or_not_migrated(tmp_path) -> None:
    """A database predating the change still holds the old `failed` marking."""
    store, profile = _profile_and_tasks(tmp_path)
    legacy = store.create(
        request_id="conversation:legacy-1",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-legacy",
        profile=profile,
    )
    _claimed, token = store.claim(legacy.task_id)
    store.fail(
        legacy.task_id,
        runner_token=token,
        error_code="model_content_policy_not_current_or_enabled",
    )
    current = store.create(
        request_id="conversation:current-1",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-current",
        profile=profile,
    )
    _claimed, token = store.claim(current.task_id)
    store.stop(
        current.task_id,
        runner_token=token,
        error_code="model_content_policy_not_current_or_enabled",
    )

    assert store.unexecuted_policy_failures() == 2

    assert store.reclassify_policy_stopped() == 1
    assert store.unexecuted_policy_failures() == 2


def test_reclassifying_is_bounded(tmp_path) -> None:
    """A large backlog must not convert in one long write."""
    store, profile = _profile_and_tasks(tmp_path)
    for index in range(3):
        task = store.create(
            request_id=f"conversation:stopped-{index}",
            user_id="local",
            kind=MemoryTaskKind.EXTRACT,
            input_ref=f"evidence-{index}",
            profile=profile,
        )
        _claimed, token = store.claim(task.task_id)
        store.fail(
            task.task_id,
            runner_token=token,
            error_code="model_content_policy_not_current_or_enabled",
        )

    assert store.reclassify_policy_stopped(limit=2) == 2
    assert store.reclassify_policy_stopped(limit=2) == 1
    assert store.reclassify_policy_stopped(limit=2) == 0


def test_reclassifying_rejects_a_non_positive_limit(tmp_path) -> None:
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    with pytest.raises(ValueError):
        store.reclassify_policy_stopped(limit=0)


def test_a_failed_task_stops_being_claimed_at_the_retry_ceiling(tmp_path) -> None:
    """A permanently failing task must not be re-run forever.

    `failed` is retryable, but with no ceiling a broken task is re-claimed on
    every dispatch, burning model calls and holding a harness slot. The
    attempts column counts the re-claims; at the ceiling the task is terminal.
    """
    store = MemoryTaskStore(tmp_path / "one-memory.sqlite3")
    _seed_task(store, "failed-1", MemoryTaskStatus.FAILED)
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET attempts=? WHERE task_id=?",
            (3, "failed-1"),
        )

    with pytest.raises(MemoryTaskOwnershipError, match="retry budget exhausted"):
        store.claim("failed-1")


def test_a_failed_task_below_the_ceiling_is_still_claimable(tmp_path) -> None:
    """The ceiling must not stop legitimate retries."""
    database = tmp_path / "one-memory.sqlite3"
    secrets = InMemorySecretStore()
    profiles = ModelProfileStore(database)
    store = MemoryTaskStore(database)
    profile = activate(
        profiles,
        profiles.create(
            ModelProfileInput(
                name="Local",
                provider_kind=ProviderKind.OLLAMA,
                model="qwen",
                embedding_model="nomic-embed-text",
            ),
            secrets,
        ),
        secrets,
    )
    task = store.create(
        request_id="extract:evidence-2",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-2",
        profile=profile,
    )
    _claimed, token = store.claim(task.task_id)
    store.fail(task.task_id, runner_token=token, error_code="model_content_execution_failed")
    with sqlite3.connect(store.database_path) as connection, connection:
        connection.execute(
            "UPDATE memory_tasks SET attempts=? WHERE task_id=?",
            (2, task.task_id),
        )

    claimed, _token = store.claim(task.task_id)
    assert claimed.status is MemoryTaskStatus.RUNNING
    assert claimed.attempts == 3


def test_the_retry_ceiling_is_a_finite_fixed_bound() -> None:
    """The bound is a product decision, not whatever the implementation says.

    A test that imports `TASK_MAX_ATTEMPTS` and uses it to construct the
    "already exhausted" row follows the implementation: mutating the constant
    to a huge value leaves the test green while permanently failed tasks become
    effectively immortal. Assert the concrete boundary here, independently of
    the module constant.
    """
    from one_memory.memory_tasks import TASK_MAX_ATTEMPTS

    assert TASK_MAX_ATTEMPTS == 3


def test_supersede_unstarted_retires_a_parked_task_of_a_shorter_chunk(tmp_path) -> None:
    """A parked-but-never-sent task is unstarted work and must be superseded.

    `supersede_unstarted` exists so a live conversation's growth step does not
    leave the earlier, shorter chunk queued behind it. It currently only looks
    at `PENDING`. A task parked as `WAITING_FOR_PERMISSION`/`WAITING_FOR_MODEL`
    has equally never reached HY; when permission returns it is re-admitted and
    sent, even though a longer full-coverage chunk already exists. That is the
    duplicate the supersede rule is meant to prevent.
    """
    tasks, profile = _profile_and_tasks(tmp_path)
    prefix = f"conversation:test:{profile.profile_id}:{profile.revision}:"
    shorter = tasks.create(
        request_id=f"{prefix}a1",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=profile,
    )
    # The shorter chunk covers e1; park it after a permission refusal.
    with sqlite3.connect(tasks.database_path) as connection:
        connection.execute(
            "UPDATE memory_tasks SET status=? WHERE task_id=?",
            (str(MemoryTaskStatus.WAITING_FOR_PERMISSION), shorter.task_id),
        )
        connection.execute(
            "INSERT OR REPLACE INTO memory_task_inputs VALUES (?,?)",
            (shorter.task_id, '[{"evidence_id":"e1"}]'),
        )
    longer = tasks.create(
        request_id=f"{prefix}a2",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=profile,
    )
    with sqlite3.connect(tasks.database_path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO memory_task_inputs VALUES (?,?)",
            (longer.task_id, '[{"evidence_id":"e1"},{"evidence_id":"e2"}]'),
        )

    retired = tasks.supersede_unstarted(longer, request_prefix=prefix)

    assert retired == 1
    assert tasks.require(shorter.task_id).status is MemoryTaskStatus.CANCELLED
    assert tasks.require(longer.task_id).status is MemoryTaskStatus.PENDING


def test_supersede_unstarted_retires_a_parked_task_that_was_claimed_once(tmp_path) -> None:
    """A runner-parked task has attempts > 0 but is still unsent.

    `wait_for_permission` runs after `claim()` (attempts already incremented),
    and permission-required is raised before the bridge, so the task never
    reached HY. Requiring `attempts == 0` would therefore miss exactly the
    parked tasks the supersede rule most needs to retire.
    """
    tasks, profile = _profile_and_tasks(tmp_path)
    prefix = f"conversation:test:{profile.profile_id}:{profile.revision}:"
    shorter = tasks.create(
        request_id=f"{prefix}b1",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=profile,
    )
    with sqlite3.connect(tasks.database_path) as connection:
        connection.execute(
            "UPDATE memory_tasks SET status=?, attempts=1 WHERE task_id=?",
            (str(MemoryTaskStatus.WAITING_FOR_PERMISSION), shorter.task_id),
        )
        connection.execute(
            "INSERT OR REPLACE INTO memory_task_inputs VALUES (?,?)",
            (shorter.task_id, '[{"evidence_id":"e1"}]'),
        )
    longer = tasks.create(
        request_id=f"{prefix}b2",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref="evidence-1",
        profile=profile,
    )
    with sqlite3.connect(tasks.database_path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO memory_task_inputs VALUES (?,?)",
            (longer.task_id, '[{"evidence_id":"e1"},{"evidence_id":"e2"}]'),
        )

    retired = tasks.supersede_unstarted(longer, request_prefix=prefix)

    assert retired == 1
    assert tasks.require(shorter.task_id).status is MemoryTaskStatus.CANCELLED
