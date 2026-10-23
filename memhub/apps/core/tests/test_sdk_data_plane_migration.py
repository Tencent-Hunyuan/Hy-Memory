from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hy_memory.models.memory import MemoryLayer, MemoryStatus

from one_memory.database_access import close_main_databases, main_database
from one_memory.hy_sdk_migration import HySdkMigration
from one_memory.hy_sdk_types import (
    HySdkMemoryPage,
    HySdkMemoryRecord,
    HySdkRawPage,
    HySdkRawRecord,
)
from one_memory.memory_server.contracts import BatchIdentity, WriteRequest
from one_memory.memory_server.durable_queue import DurableWriteQueue
from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
from one_memory.memory_server.migrations import MIGRATIONS
from one_memory.sdk_data_plane_migration import (
    DataPlaneMigrationError,
    MemoryAuthority,
    SdkDataPlaneMigrationStore,
)
from one_memory.sdk_memory_runtime import SdkMemoryRuntime
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
    activate_sdk_migration_staging,
    commit_reconciled_cutover,
    prepare_legacy_write_queue_for_cutover,
    prepare_sdk_migration_staging,
    recover_sdk_migration_swap,
    resume_legacy_cleanup,
    run_sdk_data_plane_backfill,
    sdk_cutover_blockers,
    sdk_migration_staging,
)
from one_memory.target_schema import legacy_tables_present


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


def test_startup_cutover_freezes_pending_job_input_and_preserves_the_queue(store):
    _start(store)
    store.set_phase("m1", phase="reconciled", state="ready")
    data_dir = store.database_path.parent
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "INSERT INTO evidence (evidence_id,idempotency_key,user_id,source_id,"
            "conversation_id,content_kind,role,written_by,verbatim,content_hash,"
            "revision,imported_at,updated_at,ingest_seq) "
            "VALUES ('e1','e1','local','cursor','session-1','raw_conversation',"
            "'user','person','pending input',?,1,"
            "'2026-10-22T00:00:00+00:00','2026-10-22T00:00:00+00:00',1)",
            (content_hash("pending input"),),
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
    create_sdk_migration_bundle(
        data_dir=data_dir,
        bundle_id="bundle-1",
        runtime_build="build-13",
        runtime_stopped=True,
    )
    marker = commit_reconciled_cutover(
        data_dir=data_dir,
        database_path=store.database_path,
        migration_id="m1",
        bundle_id="bundle-1",
        sdk_version="2.0.5",
    )
    assert marker.migration_id == "m1"
    assert legacy_tables_present(store.database_path)
    resume_legacy_cleanup(data_dir=data_dir, database_path=store.database_path)
    assert legacy_tables_present(store.database_path) == ()
    assert store.authority() is MemoryAuthority.SDK
    preserved = DurableWriteQueue(store.database_path, sdk_authority=True)
    assert preserved.get("request-1").state == "pending"
    assert preserved.payload_for(job.job_id).input_records[0].verbatim == "pending input"


def test_unstarted_legacy_task_moves_to_surviving_write_queue(store):
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "INSERT INTO evidence (evidence_id,idempotency_key,user_id,source_id,"
            "conversation_id,content_kind,role,written_by,verbatim,content_hash,"
            "revision,imported_at,updated_at,ingest_seq) "
            "VALUES ('e2','e2','local','cursor','session-2','raw_conversation',"
            "'user','person','legacy task input',?,1,"
            "'2026-10-22T00:00:00+00:00','2026-10-22T00:00:00+00:00',2)",
            (content_hash("legacy task input"),),
        )
        database.execute(
            "INSERT INTO memory_tasks "
            "(task_id,request_id,user_id,kind,input_ref,status,profile_id,"
            "profile_revision,profile_snapshot_json,runner_token,attempts,"
            "last_error_code,created_at,updated_at,duration_seconds,hy_invocation_count) "
            "VALUES ('task-legacy','request-legacy','local','extract','e2','pending',"
            "'profile',1,'{}',NULL,0,NULL,'now','now',NULL,0)"
        )
        database.execute(
            "INSERT INTO memory_task_inputs(task_id,refs_json) VALUES (?,?)",
            (
                "task-legacy",
                json.dumps(
                    [
                        {
                            "evidence_id": "e2",
                            "revision": 1,
                            "content_hash": content_hash("legacy task input"),
                        }
                    ]
                ),
            ),
        )

    assert prepare_legacy_write_queue_for_cutover(store.database_path) == (0, 1)

    queue = DurableWriteQueue(store.database_path)
    migrated = queue.get("legacy-task:task-legacy")
    assert migrated is not None and migrated.state == "pending"
    assert queue.payload_for(migrated.job_id).input_records[0].verbatim == "legacy task input"
    with main_database(store.database_path).read() as database:
        assert database.execute(
            "SELECT status,last_error_code FROM memory_tasks WHERE task_id='task-legacy'"
        ).fetchone() == ("cancelled", "migrated_to_memory_write_job")


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


def test_cutover_ignores_unpublished_matter_placeholders(store):
    with main_database(store.database_path).write() as database, database:
        database.execute("INSERT INTO matters(matter_id,version,needs_update) VALUES ('m1',1,0)")

    assert "matter_references" not in sdk_cutover_blockers(store.database_path)


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
            "INSERT INTO governed_memory_supersessions VALUES ('legacy-old','legacy-new','now')"
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
    productivity = {
        "coding_experience": ("experience", "coding"),
        "working_experience": ("experience", "working"),
        "coding_fact": ("fact", "coding"),
        "working_fact": ("fact", None),
    }.get(layer)
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
        productivity_kind=productivity[0] if productivity else None,
        productivity_variant=productivity[1] if productivity else None,
    )


def _sdk(
    memory_id,
    *,
    content="body",
    status="active",
    productivity_kind=None,
    productivity_variant=None,
):
    return SdkMemoryInventory(
        memory_id=memory_id,
        user_id="local",
        agent_id="one-memory",
        content_hash=content_hash(content),
        status=status,
        productivity_kind=productivity_kind,
        productivity_variant=productivity_variant,
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
    assert by_key["sdk:chat:sdk-only"].action == "adopt"
    assert len(decisions) == 8


class _MigrationClient:
    def __init__(self, memories=()):
        self.memories = tuple(memories)
        self.imported = []
        self.imported_raw = []
        self.deleted = []
        self.deleted_productivity = []
        self.deleted_raw = []
        self.raw = ()
        self.productivity = {}

    async def list_memories(self, *, limit, offset, **_kwargs):
        page = self.memories[offset : offset + limit]
        return HySdkMemoryPage(
            memories=page,
            total=len(self.memories),
            limit=limit,
            offset=offset,
        )

    async def write_legacy_memory(self, **kwargs):
        self.imported.append(kwargs)
        return {"success": True, "memory_id": kwargs["memory_id"]}

    async def delete(self, memory_id):
        self.deleted.append(memory_id)
        return {"success": True, "deleted_count": 1}

    async def read_productivity_memory(self, memory_id, *, kind, variant):
        if variant is not None:
            memory = self.productivity.get((memory_id, kind, variant))
            return None if memory is None else (memory, variant)
        for candidate in ("working", "coding"):
            memory = self.productivity.get((memory_id, kind, candidate))
            if memory is not None:
                return memory, candidate
        return None

    async def list_productivity_memories(self):
        return tuple(
            {
                "memory_id": memory.memory_id,
                "content": memory.content,
                "status": memory.status or "active",
                "user_id": memory.user_id,
                "agent_id": memory.agent_id,
                "kind": kind,
                "variant": variant,
            }
            for (_memory_id, kind, variant), memory in self.productivity.items()
        )

    async def delete_productivity_memory(self, memory_id, *, kind, variant, user_id):
        self.deleted_productivity.append((memory_id, kind, variant, user_id))
        return {"success": True, "deleted_count": 1}

    async def read_raw_inventory(self, *, limit, offset, **_kwargs):
        rows = self.raw[offset : offset + limit]
        return HySdkRawPage(
            raw=rows,
            total=len(self.raw),
            limit=limit,
            offset=offset,
        )

    async def write_legacy_raw(self, **kwargs):
        self.imported_raw.append(kwargs)
        return {"success": True, "raw_id": kwargs["raw_id"]}

    async def delete_raw(self, raw_id):
        self.deleted_raw.append(raw_id)
        return {"success": True, "deleted_count": 1}

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
    client = _MigrationClient(
        (
            _record("active"),
            _record("old", status="superseded"),
            _record("shadow", status="shadow"),
        )
    )
    inventory = asyncio.run(read_sdk_memory_inventory(client, page_size=2))
    assert [(item.memory_id, item.status) for item in inventory] == [
        ("active", "active"),
        ("old", "superseded"),
        ("shadow", "shadow"),
    ]


def test_sdk_inventory_adopts_mapped_productivity_ids_and_deletes_user_removed_fact(store):
    active = _legacy("exp", layer="coding_experience")
    deleted = _legacy("fact", layer="working_fact", status="deleted")
    client = _MigrationClient()
    client.productivity = {
        ("exp", "experience", "coding"): HySdkMemoryRecord(
            memory_id="exp",
            content="sdk experience",
            layer="experience",
            status="active",
            is_latest=True,
            user_id="local",
            agent_id="scope",
        ),
        ("fact", "fact", "coding"): HySdkMemoryRecord(
            memory_id="fact",
            content="sdk fact",
            layer="fact",
            status="active",
            is_latest=True,
            user_id="local",
            agent_id="scope",
        ),
        ("sdk-only", "experience", "working"): HySdkMemoryRecord(
            memory_id="sdk-only",
            content="sdk only",
            layer="experience",
            status="active",
            is_latest=True,
            user_id="local",
            agent_id="another-scope",
        ),
    }
    sdk = asyncio.run(read_sdk_memory_inventory(client, legacy=(active, deleted)))
    _start(store)
    decisions = record_memory_migration_plan(
        store,
        migration_id="m1",
        legacy=(active, deleted),
        sdk=sdk,
    )
    by_key = {decision.item_key: decision for decision in decisions}
    assert by_key["memory:exp"].action == "matched"
    assert by_key["memory:fact"].action == "delete"
    assert by_key["sdk:working:experience:sdk-only"].action == "adopt"

    asyncio.run(
        execute_memory_migration_plan(
            client,
            store,
            migration_id="m1",
            legacy=(active, deleted),
            decisions=decisions,
        )
    )
    assert client.imported == []
    assert client.deleted_productivity == [("fact", "fact", None, "local")]


def test_productivity_id_in_both_variants_is_quarantined():
    legacy = (_legacy("fact", layer="working_fact"),)
    sdk = (
        _sdk(
            "fact",
            productivity_kind="fact",
            productivity_variant="working",
        ),
        _sdk(
            "fact",
            productivity_kind="fact",
            productivity_variant="coding",
        ),
    )
    (decision, *_sdk_only) = classify_memory_inventory(legacy, sdk)
    assert decision.action == "quarantine"
    assert decision.reason == "sdk_memory_id_ambiguous"


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
    client = _MigrationClient()
    asyncio.run(
        execute_memory_migration_plan(
            client,
            store,
            migration_id="m1",
            legacy=legacy,
            decisions=decisions,
        )
    )

    assert [item["memory_id"] for item in client.imported] == ["missing"]
    assert client.deleted == ["delete-existing"]
    progress = store.progress("m1")
    assert progress.counts == {"adopted": 1, "deleted": 2, "verified": 3}


def test_legacy_raw_inventory_builds_recent_user_assistant_turns_without_tools(store):
    database = store.database_path
    with main_database(database).write() as connection, connection:
        connection.executemany(
            """
            INSERT INTO evidence (
                evidence_id,idempotency_key,user_id,source_id,workspace,project,
                conversation_id,message_id,source_ordinal,content_kind,role,
                written_by,created_at,verbatim,content_hash,revision,
                imported_at,updated_at,ingest_seq
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                (
                    "u1",
                    "k1",
                    "local",
                    "cursor",
                    "/repo",
                    "project",
                    "session",
                    "m1",
                    "1",
                    "raw_conversation",
                    "user",
                    "person",
                    "2026-10-22T00:00:00+00:00",
                    "question",
                    content_hash("question"),
                    1,
                    "2026-10-22T00:00:00+00:00",
                    "2026-10-22T00:00:00+00:00",
                    1,
                ),
                (
                    "t1",
                    "k2",
                    "local",
                    "cursor",
                    "/repo",
                    "project",
                    "session",
                    "m2",
                    "2",
                    "raw_conversation",
                    "tool",
                    "tool",
                    "2026-10-22T00:00:01+00:00",
                    "secret tool output",
                    content_hash("secret tool output"),
                    1,
                    "2026-10-22T00:00:01+00:00",
                    "2026-10-22T00:00:01+00:00",
                    2,
                ),
                (
                    "a1",
                    "k3",
                    "local",
                    "cursor",
                    "/repo",
                    "project",
                    "session",
                    "m3",
                    "3",
                    "raw_conversation",
                    "assistant",
                    "agent",
                    "2026-10-22T00:00:02+00:00",
                    "answer",
                    content_hash("answer"),
                    1,
                    "2026-10-22T00:00:02+00:00",
                    "2026-10-22T00:00:02+00:00",
                    3,
                ),
                (
                    "old-u",
                    "k4",
                    "local",
                    "cursor",
                    "/repo",
                    "project",
                    "old-session",
                    "m4",
                    "4",
                    "raw_conversation",
                    "user",
                    "person",
                    "2026-10-01T00:00:00+00:00",
                    "old question",
                    content_hash("old question"),
                    1,
                    "2026-10-01T00:00:00+00:00",
                    "2026-10-01T00:00:00+00:00",
                    4,
                ),
                (
                    "old-a",
                    "k5",
                    "local",
                    "cursor",
                    "/repo",
                    "project",
                    "old-session",
                    "m5",
                    "5",
                    "raw_conversation",
                    "assistant",
                    "agent",
                    "2026-10-01T00:00:01+00:00",
                    "old answer",
                    content_hash("old answer"),
                    1,
                    "2026-10-01T00:00:01+00:00",
                    "2026-10-01T00:00:01+00:00",
                    5,
                ),
            ),
        )
    (raw,) = read_legacy_raw_inventory(
        database,
        cutoff=datetime.fromisoformat("2026-10-20T00:00:00+00:00"),
    )
    assert raw.evidence_id.startswith("turn:")
    assert raw.session_id == "session"
    assert raw.source == "cursor"
    assert raw.agent_id == "all-apps"
    assert raw.content == "[user]: question\n[assistant]: answer"
    assert raw.metadata["evidence_ids"] == ["u1", "a1"]
    assert "tool" not in raw.content
    assert raw.memory_at.isoformat() == "2026-10-22T00:00:00+00:00"


def test_raw_turn_cutoff_uses_completion_time_and_keeps_workspaces_separate(store):
    database = store.database_path
    rows = (
        ("u0", "k0", "shared", "w0", "user", "person", "before", "2026-10-19T23:59:00+00:00", 1),
        ("a0", "k1", "shared", "w0", "assistant", "agent", "after", "2026-10-20T00:01:00+00:00", 2),
        ("u1", "k2", "same-session", "w1", "user", "person", "one", "2026-10-21T00:00:00+00:00", 3),
        (
            "a1",
            "k3",
            "same-session",
            "w1",
            "assistant",
            "agent",
            "first",
            "2026-10-21T00:01:00+00:00",
            4,
        ),
        ("u2", "k4", "same-session", "w2", "user", "person", "two", "2026-10-21T00:02:00+00:00", 5),
        (
            "a2",
            "k5",
            "same-session",
            "w2",
            "assistant",
            "agent",
            "second",
            "2026-10-21T00:03:00+00:00",
            6,
        ),
    )
    with main_database(database).write() as connection, connection:
        connection.executemany(
            """
            INSERT INTO evidence (
                evidence_id,idempotency_key,user_id,source_id,conversation_id,
                workspace,content_kind,role,written_by,verbatim,content_hash,
                revision,created_at,imported_at,updated_at,ingest_seq
            ) VALUES (?,?,'local','cursor',?,?,'raw_conversation',?,?,?,? ,1,?,?,?,?)
            """,
            tuple(
                (
                    evidence_id,
                    key,
                    session,
                    workspace,
                    role,
                    written_by,
                    text,
                    content_hash(text),
                    timestamp,
                    timestamp,
                    timestamp,
                    sequence,
                )
                for (
                    evidence_id,
                    key,
                    session,
                    workspace,
                    role,
                    written_by,
                    text,
                    timestamp,
                    sequence,
                ) in rows
            ),
        )

    turns = read_legacy_raw_inventory(
        database,
        cutoff=datetime.fromisoformat("2026-10-20T00:00:00+00:00"),
    )
    assert {turn.content for turn in turns} == {
        "[user]: before\n[assistant]: after",
        "[user]: one\n[assistant]: first",
        "[user]: two\n[assistant]: second",
    }
    assert {turn.metadata["workspace"] for turn in turns} == {"w0", "w1", "w2"}


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


def test_raw_inventory_matches_turns_imports_missing_and_deletes_noncanonical_sdk_raw(store):
    legacy = (_legacy_raw("e1"), _legacy_raw("e2", content="missing"))
    sdk = (_sdk_raw("raw-existing"), _sdk_raw("raw-sdk-only", content="sdk-only"))
    decisions = classify_raw_inventory(legacy, sdk)
    by_key = {item.item_key: item for item in decisions}
    assert by_key["raw:e1"].action == "matched"
    assert by_key["raw:e1"].raw_id == "raw-existing"
    assert by_key["raw:e2"].action == "import_exact"
    assert by_key["raw:e2"].raw_id == "evidence:e2"
    assert by_key["sdk-raw:raw-sdk-only"].action == "delete"

    _start(store)
    recorded = record_raw_migration_plan(
        store,
        migration_id="m1",
        legacy=legacy,
        sdk=sdk,
    )
    client = _MigrationClient()
    asyncio.run(
        execute_raw_migration_plan(
            client,
            store,
            migration_id="m1",
            legacy=legacy,
            decisions=recorded,
        )
    )
    assert [item["raw_id"] for item in client.imported_raw] == ["evidence:e2"]
    assert client.deleted_raw == ["raw-sdk-only"]
    assert store.progress("m1").counts == {"deleted": 1, "verified": 2}


def test_sdk_raw_inventory_pages_all_records():
    client = _MigrationClient()
    client.raw = (
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
    inventory = asyncio.run(read_sdk_raw_inventory(client, page_size=1))
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
                'user','person','verbatim',?,1,
                '2026-10-21T00:00:00+00:00','2026-10-21T00:00:00+00:00',1
            )
            """,
            (content_hash("verbatim"),),
        )
        connection.execute(
            """
            INSERT INTO evidence (
                evidence_id,idempotency_key,user_id,source_id,conversation_id,
                content_kind,role,written_by,verbatim,content_hash,revision,
                imported_at,updated_at,ingest_seq
            ) VALUES (
                'a1','assistant-key','local','cursor','session','raw_conversation',
                'assistant','agent','reply',?,1,
                '2026-10-21T00:00:01+00:00','2026-10-21T00:00:01+00:00',2
            )
            """,
            (content_hash("reply"),),
        )
    client = _MigrationClient()
    result = asyncio.run(
        run_sdk_data_plane_backfill(
            database_path=database,
            client=client,
            migration_id="migration",
            bundle_id="bundle",
            source_runtime_build="v1.0.4",
            target_sdk_version="2.0.5",
            source_schema_version=55,
            namespace_fingerprint="namespace",
            backend_fingerprint="backend",
        )
    )

    assert result.state == "ready"
    assert result.phase == "reconciled"
    assert result.counts == {"verified": 1}
    assert [item["memory_id"] for item in client.imported] == ["memory-1"]
    # raw 全丢：迁移只搬记忆，不再把 legacy 的对话重建成新数据面的 raw
    assert client.imported_raw == []
    assert store.authority() is MemoryAuthority.LEGACY
    # Simulate a process dying after the SDK side effect but before startup
    # could finish. The next inventory observes it as matched rather than
    # import_exact; that reclassification must resume the settled ledger
    # instead of conflicting with its original operation key.
    client.memories = (_record("memory-1", content="preference"),)
    resumed = asyncio.run(
        run_sdk_data_plane_backfill(
            database_path=database,
            client=client,
            migration_id="migration",
            bundle_id="bundle",
            source_runtime_build="v1.0.4",
            target_sdk_version="2.0.5",
            source_schema_version=55,
            namespace_fingerprint="namespace",
            backend_fingerprint="backend",
        )
    )
    assert resumed.state == "ready"
    assert resumed.counts == {"verified": 1}
    assert len(client.imported) == 1
    assert client.imported_raw == []


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
            client=_MigrationClient(),
            migration_id="migration",
            bundle_id="bundle",
            source_runtime_build="v1.0.4",
            target_sdk_version="2.0.5",
            source_schema_version=55,
            namespace_fingerprint="namespace",
            backend_fingerprint="backend",
        )
    )

    assert result.state == "blocked"
    assert result.phase == "review_required"
    assert result.counts == {"quarantine": 1}
    assert store.authority() is MemoryAuthority.LEGACY


class _MigrationLoop:
    def run(self, coroutine):
        return asyncio.run(coroutine)


class _MigrationVectorStore:
    def __init__(self):
        self.nodes = {}

    async def get_by_id(self, memory_id):
        return self.nodes.get(memory_id)

    async def upsert(self, node):
        self.nodes[node.node_id] = node
        return node.node_id

    async def list_by_user(self, *, user_id, layers, **_kwargs):
        return [
            node for node in self.nodes.values() if node.user_id == user_id and node.layer in layers
        ]


class _MigrationEmbedder:
    async def embed(self, _content):
        return [0.1, 0.2]


class _MigrationGraphStore:
    def __init__(self):
        self.nodes = {}

    async def upsert_memory_node(self, node):
        self.nodes[node.node_id] = node
        return node.node_id


class _MigrationFactVector:
    def __init__(self):
        self.deleted = []

    async def delete(self, memory_id):
        self.deleted.append(memory_id)
        return True


class _MigrationCodingStore:
    def __init__(self):
        self._conn = sqlite3.connect(":memory:", check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE coding_memory_meta ("
            "experience_id TEXT PRIMARY KEY,user_id TEXT,agent_id TEXT,solution TEXT)"
        )
        self._conn.execute(
            "CREATE TABLE working_fact ("
            "fact_id TEXT PRIMARY KEY,user_id TEXT,agent_id TEXT,"
            "statement TEXT,status TEXT)"
        )
        self._conn.execute(
            "INSERT INTO working_fact VALUES ('fact-1','local','scope','fact body','active')"
        )
        self._conn.commit()
        self._lock = threading.Lock()
        self._fact_vec = _MigrationFactVector()

    async def get_fact(self, memory_id, *, user_id):
        row = self._conn.execute(
            "SELECT 1 FROM working_fact WHERE fact_id=? AND user_id=?",
            (memory_id, user_id),
        ).fetchone()
        return object() if row is not None else None


class _PrivilegedMigrationClient:
    def __init__(self):
        self._loop_thread = _MigrationLoop()
        self._vector_store = _MigrationVectorStore()
        self._embed_service = _MigrationEmbedder()
        self._graph_store = _MigrationGraphStore()
        self._coding = _MigrationCodingStore()

    async def _coding_store_for_exact_get(self, variant):
        return self._coding if variant == "coding" else None

    def list_memories(self, **_kwargs):
        return {"vdb": {"memories": [], "total": 0}, "request_id": "list"}

    def delete(self, _memory_id):
        return {"success": True, "deleted_count": 1}

    def get_write_outcome(self, _request_id):
        return None


def test_offline_migration_writes_sdk_stores_without_public_client_import_methods():
    client = _PrivilegedMigrationClient()
    migration = HySdkMigration(client)

    raw_result = asyncio.run(
        migration.write_legacy_raw(
            content="verbatim",
            raw_id="raw-1",
            user_id="local",
            agent_id="one-memory",
            session_id="session",
            source="cursor",
            memory_at=None,
            metadata={"role": "user"},
            request_id="migrate-raw-1",
        )
    )
    memory_result = asyncio.run(
        migration.write_legacy_memory(
            content="prefers tea",
            memory_id="memory-1",
            user_id="local",
            agent_id="one-memory",
            session_id="session",
            source="legacy-governance",
            layer="l2_fact",
            status="active",
            owner="user",
            tags=("preference",),
            supersedes=(),
            superseded_by=(),
            is_latest=True,
            memory_at=None,
            metadata={},
            request_id="migrate-memory-1",
        )
    )
    raw_page = asyncio.run(migration.read_raw_inventory(user_id="local"))
    productivity = asyncio.run(migration.list_productivity_memories())
    deleted = asyncio.run(
        migration.delete_productivity_memory(
            "fact-1",
            kind="fact",
            variant=None,
            user_id="local",
        )
    )

    assert raw_result["raw_id"] == "raw-1"
    assert memory_result["memory_id"] == "memory-1"
    assert [item.raw_id for item in raw_page.raw] == ["raw-1"]
    assert productivity == (
        {
            "memory_id": "fact-1",
            "content": "fact body",
            "status": "active",
            "user_id": "local",
            "agent_id": "scope",
            "kind": "fact",
            "variant": "coding",
        },
    )
    assert client._vector_store.nodes["raw-1"].layer is MemoryLayer.L1_RAW
    assert client._vector_store.nodes["memory-1"].status is MemoryStatus.ACTIVE
    assert client._graph_store.nodes["memory-1"].content == "prefers tea"
    assert deleted["deleted_count"] == 1
    assert client._coding._fact_vec.deleted == ["fact-1"]
    assert client._coding._conn.execute("SELECT COUNT(*) FROM working_fact").fetchone()[0] == 0
    assert not hasattr(client, "import_raw")
    assert not hasattr(client, "import_memory_exact")


def test_raw_http_dto_uses_the_unified_memory_list_shape():
    page = HySdkRawPage.from_memory_list(
        {
            "vdb": {
                "memories": [
                    {
                        "memory_id": "raw-1",
                        "content": "verbatim",
                        "layer": "l1_raw",
                        "status": "active",
                        "user_id": "local",
                        "agent_id": "one-memory",
                        "session_id": "session",
                        "source": "cursor",
                        "custom": {"role": "user"},
                    }
                ],
                "total": 1,
                "limit": 100,
                "offset": 0,
            }
        },
        limit=100,
        offset=0,
    )
    assert page.raw[0].raw_id == "raw-1"
    assert page.raw[0].custom == {"role": "user"}


def test_sdk_raw_retention_keeps_only_the_latest_three_days():
    now = datetime(2026, 10, 23, tzinfo=UTC)

    class Client:
        def __init__(self):
            self.rows = [
                {
                    "memory_id": "old",
                    "memory_at": int((now - timedelta(days=4)).timestamp()),
                },
                {
                    "memory_id": "recent",
                    "memory_at": int((now - timedelta(days=2)).timestamp()),
                },
            ]

        def list_memories(self, **_kwargs):
            return {
                "vdb": {
                    "memories": list(self.rows),
                    "total": len(self.rows),
                    "limit": 500,
                    "offset": 0,
                }
            }

        def delete(self, memory_id):
            before = len(self.rows)
            self.rows = [row for row in self.rows if row["memory_id"] != memory_id]
            return {
                "success": True,
                "deleted_count": before - len(self.rows),
            }

    client = Client()

    class Handle:
        async def client(self):
            return client

    runtime = SdkMemoryRuntime(
        coordinator=None,
        read_handle=Handle(),
        write_queue=None,
        extraction_settings=None,
        data_dir=Path("."),
    )
    assert asyncio.run(runtime.prune_expired_raw(now=now)) == 1
    assert [row["memory_id"] for row in client.rows] == ["recent"]


def _staging_fixture(tmp_path, store):
    data_dir = tmp_path / "home"
    live = data_dir / "hy-memory" / "governed-v2"
    live.mkdir(parents=True)
    (live / "sdk-only.txt").write_text("old", encoding="utf-8")
    paths = sdk_migration_staging(
        data_dir=data_dir,
        migration_id="migration-1",
        namespace="governed-v2",
    )
    prepare_sdk_migration_staging(
        paths=paths,
        database_path=store.database_path,
    )
    return paths


def test_staging_preserves_sdk_only_data_and_swaps_after_commit(tmp_path, store):
    paths = _staging_fixture(tmp_path, store)
    staged = paths.staging_root / "sdk-only.txt"
    assert staged.read_text(encoding="utf-8") == "old"
    staged.write_text("migrated", encoding="utf-8")
    marker = object()

    result = activate_sdk_migration_staging(
        paths=paths,
        database_path=store.database_path,
        commit=lambda: marker,
    )

    assert result is marker
    assert (paths.live_root / "sdk-only.txt").read_text(encoding="utf-8") == "migrated"
    assert not paths.protective_root.exists()
    assert not paths.staging_home.exists()


def test_incomplete_staging_retry_restarts_from_unchanged_live_root(tmp_path, store):
    paths = _staging_fixture(tmp_path, store)
    (paths.staging_root / "sdk-only.txt").write_text("partial", encoding="utf-8")

    prepare_sdk_migration_staging(
        paths=paths,
        database_path=store.database_path,
    )

    assert (paths.staging_root / "sdk-only.txt").read_text(encoding="utf-8") == "old"


def test_failed_cutover_restores_live_store_and_keeps_staging(tmp_path, store):
    paths = _staging_fixture(tmp_path, store)
    (paths.staging_root / "sdk-only.txt").write_text("migrated", encoding="utf-8")

    def fail():
        raise DataPlaneMigrationError("cutover_failed")

    with pytest.raises(DataPlaneMigrationError, match="cutover_failed"):
        activate_sdk_migration_staging(
            paths=paths,
            database_path=store.database_path,
            commit=fail,
        )

    assert (paths.live_root / "sdk-only.txt").read_text(encoding="utf-8") == "old"
    assert (paths.staging_root / "sdk-only.txt").read_text(encoding="utf-8") == "migrated"


def test_restart_recovers_swap_when_authority_was_not_committed(tmp_path, store):
    paths = _staging_fixture(tmp_path, store)
    (paths.staging_root / "sdk-only.txt").write_text("migrated", encoding="utf-8")
    paths.live_root.replace(paths.protective_root)
    paths.staging_root.replace(paths.live_root)

    recover_sdk_migration_swap(
        paths=paths,
        database_path=store.database_path,
    )

    assert (paths.live_root / "sdk-only.txt").read_text(encoding="utf-8") == "old"
    assert (paths.staging_root / "sdk-only.txt").read_text(encoding="utf-8") == "migrated"
