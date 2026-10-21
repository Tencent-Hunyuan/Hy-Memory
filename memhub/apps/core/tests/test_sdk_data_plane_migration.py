from __future__ import annotations

import asyncio

import pytest

from one_memory.database_access import close_main_databases
from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
from one_memory.memory_server.migrations import MIGRATIONS
from one_memory.sdk_data_plane_migration import (
    DataPlaneMigrationError,
    MemoryAuthority,
    SdkDataPlaneMigrationStore,
)


@pytest.fixture
def store(tmp_path):
    database = tmp_path / "one-memory.sqlite3"
    asyncio.run(migrate(SqliteMigrationStore(database), MIGRATIONS))
    close_main_databases()
    return SdkDataPlaneMigrationStore(database)


def _start(store, migration_id="m1"):
    return store.start(
        migration_id=migration_id,
        source_runtime_build="build-13",
        source_sdk_version="2.0.4",
        target_sdk_version="2.0.5",
        source_schema_version=55,
        namespace_fingerprint="namespace",
        backend_fingerprint="backend",
        backup_bundle_id="bundle-1",
    )


def test_authority_stays_legacy_without_explicit_cutover(store):
    assert store.authority() is MemoryAuthority.LEGACY
    progress = _start(store)
    assert progress.phase == "preflight"
    assert progress.state == "running"
    assert store.authority() is MemoryAuthority.LEGACY


def test_start_is_idempotent_but_refuses_identity_change(store):
    first = _start(store)
    second = _start(store)
    assert second == first

    with pytest.raises(DataPlaneMigrationError) as caught:
        store.start(
            migration_id="m1",
            source_runtime_build="different",
            source_sdk_version="2.0.4",
            target_sdk_version="2.0.5",
            source_schema_version=55,
            namespace_fingerprint="namespace",
            backend_fingerprint="backend",
            backup_bundle_id="bundle-1",
        )
    assert caught.value.code == "sdk_data_plane_migration_identity_conflict"


def test_item_operation_key_is_unique_and_identity_is_immutable(store):
    _start(store)
    store.upsert_item(
        migration_id="m1",
        item_key="governance:g1",
        source_kind="governance",
        source_id="g1",
        source_hash="hash-1",
        operation_key="operation-1",
    )
    store.upsert_item(
        migration_id="m1",
        item_key="governance:g1",
        source_kind="governance",
        source_id="g1",
        source_hash="hash-1",
        operation_key="operation-1",
    )

    with pytest.raises(DataPlaneMigrationError) as caught:
        store.upsert_item(
            migration_id="m1",
            item_key="governance:g2",
            source_kind="governance",
            source_id="g2",
            source_hash="hash-2",
            operation_key="operation-1",
        )
    assert caught.value.code == "sdk_data_plane_migration_operation_conflict"


def test_cutover_requires_ready_phase_and_terminal_items(store):
    _start(store)
    store.upsert_item(
        migration_id="m1",
        item_key="raw:e1",
        source_kind="raw",
        source_id="e1",
        source_hash="hash",
        operation_key="operation",
    )
    with pytest.raises(DataPlaneMigrationError) as caught:
        store.commit_cutover(
            migration_id="m1",
            sdk_version="2.0.5",
            store_version="1",
            backup_bundle_id="bundle-1",
            reconciliation_digest="digest",
        )
    assert caught.value.code == "sdk_data_plane_migration_not_ready"

    store.set_phase("m1", phase="reconciled", state="ready")
    with pytest.raises(DataPlaneMigrationError) as caught:
        store.commit_cutover(
            migration_id="m1",
            sdk_version="2.0.5",
            store_version="1",
            backup_bundle_id="bundle-1",
            reconciliation_digest="digest",
        )
    assert caught.value.code.startswith("sdk_data_plane_migration_items_unresolved:")

    store.settle_item(
        migration_id="m1",
        item_key="raw:e1",
        state="verified",
        target_memory_id="raw-1",
        target_hash="hash",
    )
    marker = store.commit_cutover(
        migration_id="m1",
        sdk_version="2.0.5",
        store_version="1",
        backup_bundle_id="bundle-1",
        reconciliation_digest="digest",
    )
    assert marker.migration_id == "m1"
    assert store.authority() is MemoryAuthority.SDK


def test_alias_is_idempotent_but_cannot_be_retargeted(store):
    _start(store)
    store.record_alias(
        migration_id="m1",
        legacy_memory_id="legacy",
        canonical_memory_id="sdk",
    )
    store.record_alias(
        migration_id="m1",
        legacy_memory_id="legacy",
        canonical_memory_id="sdk",
    )
    with pytest.raises(DataPlaneMigrationError) as caught:
        store.record_alias(
            migration_id="m1",
            legacy_memory_id="legacy",
            canonical_memory_id="other",
        )
    assert caught.value.code == "sdk_data_plane_alias_conflict"
