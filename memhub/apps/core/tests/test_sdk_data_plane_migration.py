from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from one_memory.database_access import close_main_databases, main_database
from one_memory.hy_memory_bridge import HySdkMemoryPage, HySdkMemoryRecord, HySdkRawRecord
from one_memory.matters.models import MatterPatch
from one_memory.matters.sdk_store import SdkMatterStore
from one_memory.memory_server.contracts import BatchIdentity, WriteRequest
from one_memory.memory_server.durable_queue import COMPLETED, DurableWriteQueue
from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
from one_memory.memory_server.migrations import MIGRATIONS
from one_memory.sdk_data_plane_migration import (
    DataPlaneMigrationError,
    MemoryAuthority,
    SdkDataPlaneMigrationStore,
)
from one_memory.sdk_migration_bundle import create_sdk_migration_bundle
from one_memory.sdk_migration_inventory import (
    LegacyMemoryInventory,
    LegacyRawInventory,
    SdkMemoryInventory,
    SdkRawInventory,
    classify_memory_inventory,
    classify_raw_inventory,
    content_hash,
    execute_memory_migration_plan,
    execute_raw_migration_plan,
    read_legacy_memory_inventory,
    read_legacy_raw_inventory,
    read_sdk_memory_inventory,
    read_sdk_raw_inventory,
    record_memory_migration_plan,
    record_raw_migration_plan,
)
from one_memory.sdk_migration_runner import (
    commit_reconciled_cutover,
    run_sdk_data_plane_backfill,
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


def test_startup_cutover_requires_verified_bundle_and_no_pending_sdk_call(store):
    _start(store)
    store.set_phase("m1", phase="reconciled", state="ready")
    data_dir = store.database_path.parent
    create_sdk_migration_bundle(
        data_dir=data_dir,
        bundle_id="bundle-1",
        runtime_build="build-13",
        runtime_stopped=True,
    )
    queue = DurableWriteQueue(store.database_path)
    request = WriteRequest(
        internal_session_id="session-1",
        source_id="cursor",
        batch_identity=BatchIdentity(
            internal_session_id="session-1",
            first_round=1,
            last_round=1,
        ),
        evidence_ids=["e1"],
        idempotency_key="request-1",
    )
    job = queue.submit(request)

    with pytest.raises(DataPlaneMigrationError, match="write_jobs"):
        commit_reconciled_cutover(
            data_dir=data_dir,
            database_path=store.database_path,
            migration_id="m1",
            bundle_id="bundle-1",
            sdk_version="2.0.5.dev3",
            product_paths_ready=True,
        )

    queue.settle(job.job_id, state=COMPLETED)
    marker = commit_reconciled_cutover(
        data_dir=data_dir,
        database_path=store.database_path,
        migration_id="m1",
        bundle_id="bundle-1",
        sdk_version="2.0.5.dev3",
        product_paths_ready=True,
    )
    assert marker.migration_id == "m1"


def test_alias_is_idempotent_but_cannot_be_retargeted(store):
    _start(store)
    store.record_alias(
        migration_id="m1",
        legacy_memory_id="legacy",
        canonical_memory_id="sdk",
    )
    store.record_alias(
        migration_id="m2",
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
        self.imported_raw = []
        self.deleted = []
        self.raw = ()

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

    async def list_sdk_raw(self, *, limit, offset, **_kwargs):
        rows = self.raw[offset : offset + limit]
        return rows, len(self.raw)

    async def import_sdk_raw_exact(self, **kwargs):
        self.imported_raw.append(kwargs)
        return {"success": True, "raw_id": kwargs["raw_id"]}

    async def get_sdk_memory(self, memory_id):
        return next(
            (memory for memory in self.memories if memory.memory_id == memory_id),
            None,
        )

    async def get_sdk_raw(self, raw_id):
        return next((raw for raw in self.raw if raw.raw_id == raw_id), None)


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


def test_legacy_raw_inventory_preserves_exact_source_metadata(store):
    database = store.database_path
    with main_database(database).write() as connection, connection:
        connection.execute(
            """
            INSERT INTO evidence (
                evidence_id,idempotency_key,user_id,source_id,workspace,project,
                conversation_id,message_id,source_ordinal,content_kind,role,
                written_by,created_at,verbatim,content_hash,revision,
                imported_at,updated_at,ingest_seq
            ) VALUES (
                'e1','key','local','cursor','/repo','project','session','message',
                '7','raw_conversation','user','person','2026-10-01T00:00:00+00:00',
                'verbatim',?,2,'2026-10-02T00:00:00+00:00',
                '2026-10-02T00:00:00+00:00',1
            )
            """,
            (content_hash("verbatim"),),
        )
    (raw,) = read_legacy_raw_inventory(database)
    assert raw.evidence_id == "e1"
    assert raw.session_id == "session"
    assert raw.source == "cursor"
    assert raw.metadata["role"] == "user"
    assert raw.metadata["revision"] == 2
    assert raw.memory_at.isoformat() == "2026-10-01T00:00:00+00:00"


def _legacy_raw(evidence_id, *, content="verbatim"):
    return LegacyRawInventory(
        evidence_id=evidence_id,
        user_id="local",
        agent_id="one-memory",
        session_id="session",
        source="cursor",
        content=content,
        content_hash=content_hash(content),
        memory_at=None,
        metadata={"role": "user"},
    )


def _sdk_raw(raw_id, *, content="verbatim"):
    return SdkRawInventory(
        raw_id=raw_id,
        user_id="local",
        agent_id="one-memory",
        session_id="session",
        source="cursor",
        content_hash=content_hash(content),
    )


def test_raw_inventory_matches_exact_scope_imports_missing_and_adopts_sdk_only(store):
    legacy = (_legacy_raw("e1"), _legacy_raw("e2", content="missing"))
    sdk = (_sdk_raw("raw-existing"), _sdk_raw("raw-sdk-only", content="sdk-only"))
    decisions = classify_raw_inventory(legacy, sdk)
    by_key = {item.item_key: item for item in decisions}
    assert by_key["raw:e1"].action == "matched"
    assert by_key["raw:e1"].raw_id == "raw-existing"
    assert by_key["raw:e2"].action == "import_exact"
    assert by_key["raw:e2"].raw_id == "evidence:e2"
    assert by_key["sdk-raw:raw-sdk-only"].action == "adopt"

    _start(store)
    recorded = record_raw_migration_plan(
        store,
        migration_id="m1",
        legacy=legacy,
        sdk=sdk,
    )
    bridge = _Bridge()
    asyncio.run(
        execute_raw_migration_plan(
            bridge,
            store,
            migration_id="m1",
            legacy=legacy,
            decisions=recorded,
        )
    )
    assert [item["raw_id"] for item in bridge.imported_raw] == ["evidence:e2"]
    assert store.progress("m1").counts == {"adopted": 1, "verified": 2}


def test_sdk_raw_inventory_pages_all_records():
    bridge = _Bridge()
    bridge.raw = (
        HySdkRawRecord(
            raw_id="r1",
            content="one",
            user_id="local",
            agent_id="one-memory",
            session_id="s1",
            source="cursor",
        ),
        HySdkRawRecord(
            raw_id="r2",
            content="two",
            user_id="local",
            agent_id="one-memory",
            session_id="s2",
            source="claude",
        ),
    )
    inventory = asyncio.run(read_sdk_raw_inventory(bridge, page_size=1))
    assert [item.raw_id for item in inventory] == ["r1", "r2"]


def test_offline_backfill_reaches_ready_without_committing_cutover(store):
    database = store.database_path
    with main_database(database).write() as connection, connection:
        connection.execute(
            """
            INSERT INTO governed_memories (
                memory_id,user_id,kind,status,content,scopes_json,origin,
                tags_json,revision,created_at,updated_at
            ) VALUES (
                'memory-1','local','chat','confirmed','preference',
                '[\"all-apps\"]','extracted','[]',1,'now','now'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO evidence (
                evidence_id,idempotency_key,user_id,source_id,conversation_id,
                content_kind,role,written_by,verbatim,content_hash,revision,
                imported_at,updated_at,ingest_seq
            ) VALUES (
                'e1','key','local','cursor','session','raw_conversation',
                'user','person','verbatim',?,1,'now','now',1
            )
            """,
            (content_hash("verbatim"),),
        )
        connection.execute(
            "INSERT INTO matter_versions(matter_id,version,body,dependencies,created_at) "
            "VALUES (?,?,?,?,?)",
            (
                "matter-1",
                1,
                json.dumps(
                    {
                        "title": "本地优先",
                        "purpose": "保持本地处理",
                        "boundary": "记忆系统",
                        "anchors": ["本地"],
                        "tags": [],
                        "uncertainties": [],
                        "source_engine": "hy-memory",
                        "claims": [
                            {
                                "kind": "constraint",
                                "scope": "记忆系统",
                                "basis": "human_statement",
                                "verification": "citation_checked",
                                "state": "current",
                                "change_note": "",
                                "statement": "preference",
                                "memory_refs": [
                                    {
                                        "memory_id": "memory-1",
                                        "content_hash": content_hash("preference"),
                                    }
                                ],
                                "citations": [
                                    {
                                        "evidence_id": "e1",
                                        "quote": "verbatim",
                                    }
                                ],
                            }
                        ],
                    }
                ),
                "[]",
                "now",
            ),
        )
        connection.execute(
            "INSERT INTO matters(matter_id,version,needs_update) VALUES ('matter-1',1,0)"
        )
    bridge = _Bridge()
    result = asyncio.run(
        run_sdk_data_plane_backfill(
            database_path=database,
            bridge=bridge,
            migration_id="migration",
            bundle_id="bundle",
            source_runtime_build="v1.0.4",
            target_sdk_version="2.0.5.dev3",
            source_schema_version=55,
            namespace_fingerprint="namespace",
            backend_fingerprint="backend",
        )
    )

    assert result.state == "ready"
    assert result.phase == "reconciled"
    assert result.counts == {"verified": 3}
    assert [item["memory_id"] for item in bridge.imported] == ["memory-1"]
    assert [item["raw_id"] for item in bridge.imported_raw] == ["evidence:e1"]
    assert store.authority() is MemoryAuthority.LEGACY
    with main_database(database).read(row_factory=sqlite3.Row) as connection:
        matter = connection.execute(
            "SELECT references_json FROM matter_sdk_versions "
            "WHERE matter_id='matter-1' AND version=1"
        ).fetchone()
    references = json.loads(str(matter["references_json"]))
    assert references["claims"][0]["memory_refs"] == [
        {
            "memory_id": "memory-1",
            "content_hash": content_hash("preference"),
        }
    ]
    assert references["claims"][0]["raw_refs"][0]["raw_id"] == "evidence:e1"
    assert "statement" not in references["claims"][0]
    assert "quote" not in references["claims"][0]["raw_refs"][0]

    # Simulate a process dying after both SDK side effects but before startup
    # could finish. The next inventory observes them as matched rather than
    # import_exact; that reclassification must resume the settled ledger
    # instead of conflicting with its original operation key.
    bridge.memories = (_record("memory-1", content="preference"),)
    bridge.raw = (
        HySdkRawRecord(
            raw_id="evidence:e1",
            content="verbatim",
            user_id="local",
            agent_id="one-memory",
            session_id="session",
            source="cursor",
            custom={"role": "user", "written_by": "person"},
        ),
    )
    sdk_matters = SdkMatterStore(database, bridge)  # type: ignore[arg-type]
    hydrated = asyncio.run(sdk_matters.get("matter-1"))
    assert hydrated.claims[0].statement == "preference"
    assert hydrated.claims[0].memory_refs[0].authority == "sdk"
    assert hydrated.claims[0].citations[0].raw_id == "evidence:e1"
    assert hydrated.claims[0].citations[0].evidence_id is None
    assert hydrated.claims[0].citations[0].quote == "verbatim"
    patched = asyncio.run(
        sdk_matters.patch(
            "matter-1",
            MatterPatch(expected_version=1, title="本地优先更新"),
        )
    )
    assert patched.version == 2
    assert patched.title == "本地优先更新"
    assert len(asyncio.run(sdk_matters.history("matter-1"))) == 2
    resumed = asyncio.run(
        run_sdk_data_plane_backfill(
            database_path=database,
            bridge=bridge,
            migration_id="migration",
            bundle_id="bundle",
            source_runtime_build="v1.0.4",
            target_sdk_version="2.0.5.dev3",
            source_schema_version=55,
            namespace_fingerprint="namespace",
            backend_fingerprint="backend",
        )
    )
    assert resumed.state == "ready"
    assert resumed.counts == {"verified": 3}
    assert len(bridge.imported) == 1
    assert len(bridge.imported_raw) == 1


def test_offline_backfill_blocks_cutover_when_an_item_is_quarantined(store):
    database = store.database_path
    with main_database(database).write() as connection, connection:
        connection.execute(
            """
            INSERT INTO governed_memories (
                memory_id,user_id,kind,status,content,scopes_json,origin,
                tags_json,revision,created_at,updated_at
            ) VALUES (
                'memory-1','local','coding','confirmed','unknown layer',
                '[\"all-apps\"]','extracted','[]',1,'now','now'
            )
            """
        )
    result = asyncio.run(
        run_sdk_data_plane_backfill(
            database_path=database,
            bridge=_Bridge(),
            migration_id="migration",
            bundle_id="bundle",
            source_runtime_build="v1.0.4",
            target_sdk_version="2.0.5.dev3",
            source_schema_version=55,
            namespace_fingerprint="namespace",
            backend_fingerprint="backend",
        )
    )

    assert result.state == "blocked"
    assert result.phase == "review_required"
    assert result.counts == {"quarantine": 1}
    assert store.authority() is MemoryAuthority.LEGACY
