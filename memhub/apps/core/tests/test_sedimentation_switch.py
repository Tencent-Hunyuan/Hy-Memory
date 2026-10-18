"""Stopping model-driven sedimentation without losing the configuration.

The switch is the user's cost control: while it is off nothing may reach the
model, but neither the model configuration nor the already imported
conversations may be touched, and work that was already queued has to survive
so starting again resumes it instead of beginning from nothing.
"""

from __future__ import annotations

import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.extraction_settings import ExtractionSettingsStore
from one_memory.hy_memory_bridge import HyMemoryBridgeError
from one_memory.imports.models import EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceInput, EvidenceStore
from one_memory.memory_processing import MemoryProcessingSupervisor
from one_memory.memory_tasks import MemoryTaskKind, MemoryTaskStatus, MemoryTaskStore
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfileInput,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore

NOW = datetime(2026, 9, 9, tzinfo=UTC)


class _Flag:
    """The user's switch, standing in for `ConfigStore.sedimentation_enabled`."""

    def __init__(self, value: bool = True) -> None:
        self.value = value

    def __call__(self) -> bool:
        return self.value


def test_sedimentation_route_persists_and_resumes_without_restart(tmp_path) -> None:
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            source_home=tmp_path / "home",
            api_token=SecretStr("test"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )
    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
        assert client.get("/api/v1/sedimentation").json()["enabled"] is True
        stopped = client.put(
            "/api/v1/sedimentation", json={"enabled": False}
        )
        assert stopped.status_code == 200
        assert stopped.json()["enabled"] is False
        assert client.get("/api/v1/sedimentation").json()["enabled"] is False
        resumed = client.put("/api/v1/sedimentation", json={"enabled": True})
        assert resumed.status_code == 200
        assert resumed.json()["enabled"] is True


def test_switch_answers_before_the_resume_finishes(tmp_path, monkeypatch) -> None:
    """The PUT is a decision; the walk over the backlog happens after it.

    Awaiting that walk is what turned "turn sedimentation back on" into a
    request that never returned. The renderer gave up at its thirty-second
    timeout and reported a failure for a switch that had in fact been saved,
    which is the worst of both: the user believes the model is still stopped
    while it is already running.
    """
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            source_home=tmp_path / "home",
            api_token=SecretStr("test"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )
    released = threading.Event()
    finished = threading.Event()

    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
        supervisor = app.state.memory_processing_supervisor

        async def slow_resume() -> None:
            await asyncio.get_running_loop().run_in_executor(None, released.wait, 10.0)
            finished.set()

        monkeypatch.setattr(supervisor, "resume_allowed", slow_resume)
        assert client.put("/api/v1/sedimentation", json={"enabled": False}).status_code == 200
        resumed = client.put("/api/v1/sedimentation", json={"enabled": True})
        assert resumed.status_code == 200
        assert resumed.json()["enabled"] is True
        # Answered while the resume is still walking the backlog.
        assert finished.is_set() is False
        released.set()


def test_a_failing_resume_does_not_fail_the_switch(tmp_path, monkeypatch) -> None:
    """Resuming is opportunistic; the user's choice is already stored."""
    app = create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            source_home=tmp_path / "home",
            api_token=SecretStr("test"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )
    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
        supervisor = app.state.memory_processing_supervisor

        async def broken_resume() -> None:
            raise RuntimeError("hy_memory_processing_unavailable")

        monkeypatch.setattr(supervisor, "resume_allowed", broken_resume)
        assert client.put("/api/v1/sedimentation", json={"enabled": False}).status_code == 200
        resumed = client.put("/api/v1/sedimentation", json={"enabled": True})
        assert resumed.status_code == 200
        assert resumed.json()["enabled"] is True


def _stores(tmp_path, flag: _Flag):
    database_path = tmp_path / "one-memory.sqlite3"
    evidence = EvidenceStore(database_path)
    tasks = MemoryTaskStore(database_path)
    profiles = ModelProfileStore(database_path)
    secrets = InMemorySecretStore()
    profile = profiles.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OPENAI_COMPATIBLE,
            base_url="https://example.com/v1",
            model="test-model",
            embedding_provider_kind=ProviderKind.LOCAL_ONNX,
            embedding_model="builtin",
            embedding_dimensions=512,
        ),
        secrets,
    )
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
    active = profiles.activate(profile.profile_id, secrets)
    supervisor = MemoryProcessingSupervisor(
        tasks=tasks,
        profiles=profiles,
        secrets=secrets,
        evidence=evidence,
        governance=None,  # type: ignore[arg-type]
        data_dir=tmp_path,
        extraction_settings=ExtractionSettingsStore(database_path),
        sedimentation_enabled=flag,
    )
    return evidence, tasks, active, supervisor, profiles, secrets


def _task(evidence, tasks, active, *, index: int, age_days: int = 1):
    record = evidence.put(
        EvidenceInput(
            source_id="cursor",
            workspace="/Users/me/project",
            conversation_id=f"conv-{index}",
            message_id=f"m{index}",
            correlation_id=f"m{index}",
            role="user",
            written_by=WrittenBy.PERSON,
            created_at=NOW - timedelta(days=age_days),
            verbatim="I prefer concise answers.",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    return tasks.create(
        request_id=f"req-{index}",
        user_id="local",
        kind=MemoryTaskKind.EXTRACT,
        input_ref=record.evidence_id,
        profile=active,
    )


def test_stopped_switch_classifies_no_work_and_keeps_it_pending(tmp_path) -> None:
    flag = _Flag(False)
    evidence, tasks, active, supervisor, _profiles, _secrets = _stores(tmp_path, flag)

    task = _task(evidence, tasks, active, index=1)

    assert supervisor._classify([task]) == []
    # Parked, not failed: nothing was sent to the model, so a stopped switch
    # must not read as a broken pipeline.
    assert tasks.require(task.task_id).status is MemoryTaskStatus.PENDING

    flag.value = True
    assert len(supervisor._classify([task])) == 1


def test_stopped_switch_refuses_direct_durable_processing(tmp_path) -> None:
    flag = _Flag(False)
    evidence, tasks, active, supervisor, _profiles, _secrets = _stores(
        tmp_path, flag
    )
    task = _task(evidence, tasks, active, index=1)
    record = evidence.get(task.input_ref)
    assert record is not None

    with pytest.raises(HyMemoryBridgeError, match="model_sedimentation_disabled"):
        asyncio.run(supervisor.process_records((record,)))

    assert tasks.require(task.task_id).status is MemoryTaskStatus.PENDING


def test_pause_returns_a_claimed_task_attempt(tmp_path) -> None:
    flag = _Flag(True)
    evidence, tasks, active, _supervisor, _profiles, _secrets = _stores(
        tmp_path, flag
    )
    task = _task(evidence, tasks, active, index=1)
    claimed, token = tasks.claim(task.task_id)
    assert claimed.attempts == 1

    released = tasks.release_unstarted(
        task.task_id,
        runner_token=token,
        error_code="model_sedimentation_disabled",
    )

    assert released.status is MemoryTaskStatus.PENDING
    assert released.attempts == 0


def test_stopping_after_scheduler_classification_starts_no_worker(tmp_path) -> None:
    flag = _Flag(True)
    evidence, tasks, active, supervisor, _profiles, _secrets = _stores(
        tmp_path, flag
    )
    task = _task(evidence, tasks, active, index=1)
    work = supervisor._classify([task])
    assert len(work) == 1
    supervisor._queue.add(work[0])

    flag.value = False
    asyncio.run(supervisor._work())

    current = tasks.require(task.task_id)
    assert current.status is MemoryTaskStatus.PENDING
    assert current.attempts == 0


def test_stopped_switch_enqueues_nothing(tmp_path) -> None:
    flag = _Flag(False)
    evidence, _tasks, active, supervisor, _profiles, _secrets = _stores(tmp_path, flag)
    record = evidence.put(
        EvidenceInput(
            source_id="cursor",
            workspace="/Users/me/project",
            conversation_id="conv-live",
            message_id="m-live",
            correlation_id="m-live",
            role="user",
            written_by=WrittenBy.PERSON,
            created_at=NOW,
            verbatim="Remember this.",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record

    assert supervisor.enqueue_evidence(record) is None
    assert supervisor._debounces == {}


def test_stopped_switch_prepares_no_backfill(tmp_path) -> None:
    flag = _Flag(False)
    evidence, tasks, active, supervisor, _profiles, _secrets = _stores(tmp_path, flag)
    # No task is queued yet: backfill is what would create one for this
    # conversation, so the stopped switch must keep it from creating any.
    # A settled turn (question plus answer): an unanswered user tail is not
    # backfill material, so the conversation needs both records.
    for message_id, role, written_by, verbatim in (
        ("m-old", "user", WrittenBy.PERSON, "Remember this too."),
        ("m-old-reply", "assistant", WrittenBy.AGENT, "Saved."),
    ):
        evidence.put(
            EvidenceInput(
                source_id="cursor",
                workspace="/Users/me/project",
                conversation_id="conv-old",
                message_id=message_id,
                correlation_id=message_id,
                role=role,
                written_by=written_by,
                created_at=NOW - timedelta(days=1),
                verbatim=verbatim,
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        )

    assert supervisor.prepare_backfill() == []

    flag.value = True
    assert len(supervisor.prepare_backfill()) == 1


def test_absent_switch_keeps_sedimenting(tmp_path) -> None:
    """A Core without the setting behaves exactly as it did before it existed."""
    evidence, tasks, active, supervisor, _profiles, _secrets = _stores(
        tmp_path, _Flag(True)
    )
    supervisor.sedimentation_enabled = None
    task = _task(evidence, tasks, active, index=1)

    assert len(supervisor._classify([task])) == 1


def test_stopping_does_not_disturb_the_model_configuration(tmp_path) -> None:
    flag = _Flag(True)
    evidence, tasks, active, supervisor, profiles, secrets = _stores(tmp_path, flag)
    _task(evidence, tasks, active, index=1)

    flag.value = False
    assert supervisor.prepare_backfill() == []
    # Still configured: starting again is a switch, not a re-entry of a key.
    assert profiles.active(secrets) is not None


def test_bounded_backfill_returns_when_the_queue_is_already_full(tmp_path) -> None:
    """A full queue means nothing to add, not "wait until it drains".

    The wait used to sleep inside the scan lock, so whoever asked for the scan
    — the resume behind a switch toggle, and through it an HTTP request —
    waited for the whole backlog to clear. A saturated queue never clears by
    waiting, so that request never came back.
    """
    flag = _Flag(True)
    evidence, tasks, active, supervisor, _profiles, _secrets = _stores(tmp_path, flag)
    for index in range(8):
        task = _task(evidence, tasks, active, index=index)
        supervisor._queue.add(supervisor._classify([task])[0])

    started = time.monotonic()
    asyncio.run(
        asyncio.wait_for(supervisor.backfill_off_thread(cohort_size=8), timeout=3)
    )
    assert time.monotonic() - started < 3


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
