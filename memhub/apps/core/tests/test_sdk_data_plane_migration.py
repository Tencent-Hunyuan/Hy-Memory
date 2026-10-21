from __future__ import annotations

import asyncio
import json

import pytest

from one_memory.database_access import close_main_databases, main_database
from one_memory.hy_memory_bridge import HySdkMemoryPage, HySdkMemoryRecord
from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
from one_memory.memory_server.migrations import MIGRATIONS
from one_memory.sdk_data_plane_migration import (
    DataPlaneMigrationError,
    MemoryAuthority,
    SdkDataPlaneMigrationStore,
)
from one_memory.sdk_migration_inventory import (
    LegacyMemoryInventory,
    SdkMemoryInventory,
    classify_memory_inventory,
    content_hash,
    execute_memory_migration_plan,
    read_legacy_memory_inventory,
    read_sdk_memory_inventory,
    record_memory_migration_plan,
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


def test_legacy_inventory_preserves_task_id_mapping_and_supersession(store):
    database = store.database_path
    with main_database(database).write() as connection, connection:
        for memory_id, status, content in (
            ("legacy-old", "superseded", "old"),
            ("legacy-new", "confirmed", "new"),
        ):
            connection.execute(
                """
                INSERT INTO governed_memories (
                    memory_id,user_id,kind,status,content,scopes_json,origin,
                    tags_json,revision,created_at,updated_at
                ) VALUES (?,?,?,?,?,'[\"all-apps\"]','extracted','[\"tag\"]',1,'now','now')
                """,
                (memory_id, "local", "chat", status, content),
            )
        connection.execute(
            "INSERT INTO governed_memory_supersessions VALUES "
            "('legacy-old','legacy-new','now')"
        )
        connection.execute(
            "INSERT INTO memory_task_outputs VALUES (?,?,?)",
            (
                "task",
                "legacy-new",
                json.dumps(
                    {
                        "task_id": "task",
                        "memory_id": "legacy-new",
                        "memory_revision": 1,
                        "hy_memory_id": "sdk-new",
                        "owner": "user",
                        "layer": "l2_fact",
                        "engine": "hy-memory",
                        "engine_version": "2.0.4",
                        "support_refs": [],
                    }
                ),
            ),
        )

    records = {item.legacy_memory_id: item for item in read_legacy_memory_inventory(database)}
    assert records["legacy-new"].canonical_memory_id == "sdk-new"
    assert records["legacy-new"].owner == "user"
    assert records["legacy-new"].supersedes == ("legacy-old",)
    assert records["legacy-old"].superseded_by == ("legacy-new",)


def _legacy(
    legacy_id,
    *,
    canonical_id=None,
    content="body",
    status="active",
    layer="l2_fact",
):
    return LegacyMemoryInventory(
        legacy_memory_id=legacy_id,
        canonical_memory_id=canonical_id or legacy_id,
        user_id="local",
        agent_id="one-memory",
        session_id="default_session",
        content=content,
        content_hash=content_hash(content),
        status=status,
        layer=layer,
        owner="user",
        tags=(),
        supersedes=(),
        superseded_by=(),
    )


def _sdk(memory_id, *, content="body", status="active"):
    return SdkMemoryInventory(
        memory_id=memory_id,
        user_id="local",
        agent_id="one-memory",
        content_hash=content_hash(content),
        status=status,
    )


def test_inventory_classification_covers_every_legacy_and_sdk_state():
    decisions = classify_memory_inventory(
        (
            _legacy("matched"),
            _legacy("mapped", canonical_id="sdk-mapped"),
            _legacy("missing"),
            _legacy("deleted", status="deleted"),
            _legacy("delete-existing", status="deleted"),
            _legacy("divergent"),
            _legacy("unknown-layer", layer=None),
        ),
        (
            _sdk("matched"),
            _sdk("sdk-mapped"),
            _sdk("delete-existing"),
            _sdk("divergent", content="different"),
            _sdk("sdk-only"),
        ),
    )
    by_key = {item.item_key: item for item in decisions}
    assert by_key["memory:matched"].action == "matched"
    assert by_key["memory:mapped"].action == "record_alias"
    assert by_key["memory:missing"].action == "import_exact"
    assert by_key["memory:deleted"].action == "verified_deleted"
    assert by_key["memory:delete-existing"].action == "delete"
    assert by_key["memory:divergent"].action == "quarantine"
    assert by_key["memory:unknown-layer"].reason == "legacy_layer_unknown"
    assert by_key["sdk:sdk-only"].action == "adopt"
    assert len(decisions) == 8


class _Bridge:
    def __init__(self, memories=()):
        self.memories = tuple(memories)
        self.imported = []
        self.deleted = []

    async def list_sdk_memories(self, *, limit, offset, **_kwargs):
        page = self.memories[offset : offset + limit]
        return HySdkMemoryPage(
            memories=page,
            total=len(self.memories),
            limit=limit,
            offset=offset,
        )

    async def import_sdk_memory_exact(self, **kwargs):
        self.imported.append(kwargs)
        return {"success": True, "memory_id": kwargs["memory_id"]}

    async def delete(self, memory_id):
        self.deleted.append(memory_id)
        return {"success": True, "deleted_count": 1}


def _record(memory_id, *, content="body", status="active"):
    return HySdkMemoryRecord(
        memory_id=memory_id,
        content=content,
        layer="l2_fact",
        status=status,
        is_latest=status == "active",
        user_id="local",
        agent_id="one-memory",
    )


def test_sdk_inventory_is_paged_without_dropping_lifecycle_records():
    bridge = _Bridge(
        (
            _record("active"),
            _record("old", status="superseded"),
            _record("shadow", status="shadow"),
        )
    )
    inventory = asyncio.run(read_sdk_memory_inventory(bridge, page_size=2))
    assert [(item.memory_id, item.status) for item in inventory] == [
        ("active", "active"),
        ("old", "superseded"),
        ("shadow", "shadow"),
    ]


def test_recorded_plan_executes_import_delete_alias_and_adoption(store):
    _start(store)
    legacy = (
        _legacy("matched"),
        _legacy("missing"),
        _legacy("mapped", canonical_id="sdk-mapped"),
        _legacy("deleted", status="deleted"),
        _legacy("delete-existing", status="deleted"),
    )
    sdk = (
        _sdk("matched"),
        _sdk("sdk-mapped"),
        _sdk("delete-existing"),
        _sdk("sdk-only"),
    )
    decisions = record_memory_migration_plan(
        store,
        migration_id="m1",
        legacy=legacy,
        sdk=sdk,
    )
    bridge = _Bridge()
    asyncio.run(
        execute_memory_migration_plan(
            bridge,
            store,
            migration_id="m1",
            legacy=legacy,
            decisions=decisions,
        )
    )

    assert [item["memory_id"] for item in bridge.imported] == ["missing"]
    assert bridge.deleted == ["delete-existing"]
    progress = store.progress("m1")
    assert progress.counts == {"adopted": 1, "deleted": 2, "verified": 3}
