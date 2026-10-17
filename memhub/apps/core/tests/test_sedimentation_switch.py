"""Stopping model-driven sedimentation without losing the configuration.

The switch is the user's cost control: while it is off nothing may reach the
model, but neither the model configuration nor the already imported
conversations may be touched, and work that was already queued has to survive
so starting again resumes it instead of beginning from nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from one_memory.extraction_settings import ExtractionSettingsStore
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
    _task(evidence, tasks, active, index=1)

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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
