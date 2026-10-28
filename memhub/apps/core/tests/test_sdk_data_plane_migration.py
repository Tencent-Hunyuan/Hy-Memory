from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory import cli
from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.database_access import close_main_databases, main_database
from one_memory.hy_sdk_migration import repair_sdk_metadata_from_bundle
from one_memory.hy_sdk_types import HySdkRawPage
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_server.contracts import BatchIdentity, WriteRequest
from one_memory.memory_server.durable_queue import DurableWriteQueue
from one_memory.memory_server.migration_runner import SqliteMigrationStore, migrate
from one_memory.memory_server.migrations import MIGRATIONS
from one_memory.runtime_client import RuntimeStatus
from one_memory.sdk_data_plane_migration import (
    DataPlaneMigrationError,
    MemoryAuthority,
    SdkDataPlaneMigrationStore,
)
from one_memory.sdk_memory_runtime import SdkMemoryRuntime
from one_memory.sdk_migration_bundle import create_sdk_migration_bundle
from one_memory.sdk_migration_runner import (
    PREPARED_PHASE,
    abandoned_legacy_work,
    commit_sdk_cutover,
    move_pending_dispatches,
    prepare_legacy_write_queue_for_cutover,
    recover_abandoned_staging,
    resume_legacy_cleanup,
    run_startup_migration,
)
from one_memory.secrets import InMemorySecretStore
from one_memory.target_schema import legacy_tables_present


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


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


def test_post_cutover_metadata_repair_restores_source_and_original_times(store):
    data_dir = store.database_path.parent
    migration_id = "sdk-authority-v2-source-repair"
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "UPDATE memory_authority_state SET authority='sdk',migration_id=? WHERE singleton=1",
            (migration_id,),
        )
    legacy = (
        data_dir
        / "backups"
        / f"{migration_id}-pre-cutover.sdk-bundle"
        / "management"
        / "one-memory.sqlite3"
    )
    legacy.parent.mkdir(parents=True)
    with sqlite3.connect(legacy) as database:
        database.executescript(
            """
            CREATE TABLE memory_task_outputs (body TEXT);
            CREATE TABLE governed_memories (
                memory_id TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE evidence (
                evidence_id TEXT, source_id TEXT, created_at TEXT, imported_at TEXT
            );
            """
        )
        database.execute(
            "INSERT INTO governed_memories VALUES (?,?,?)",
            ("legacy-1", "2026-10-18T00:00:00+00:00", "2026-10-18T01:00:00+00:00"),
        )
        database.execute(
            "INSERT INTO evidence VALUES (?,?,?,?)",
            ("e1", "workbuddy", "2254-01-01T00:00:00+00:00", "2026-10-17T00:00:00+00:00"),
        )
        database.execute(
            "INSERT INTO memory_task_outputs VALUES (?)",
            (
                json.dumps(
                    {
                        "memory_id": "legacy-1",
                        "hy_memory_id": "sdk-1",
                        "support_refs": [{"evidence_id": "e1"}],
                    }
                ),
            ),
        )

    from one_memory.sdk_vector_reattach import open_offline_chroma

    vector = data_dir / "hy-memory" / "governed-v2" / "vector"
    collection = open_offline_chroma(vector).get_or_create_collection("memory")
    collection.add(
        ids=["sdk-1"],
        documents=["remember"],
        embeddings=[[0.1, 0.2]],
        metadatas=[
            {
                "source": "default",
                "gmt_created": 1,
                "memory_at": 8_962_051_200,
            }
        ],
    )
    coding = data_dir / "hy-memory" / "governed-v2" / "coding_cv.sqlite3"
    with sqlite3.connect(coding) as database:
        # A store written by hy-memory 2.0.6, before the conversation time
        # column: the repair must add it through the SDK and then fill it.
        database.executescript(
            """
            CREATE TABLE coding_memory_meta (
                experience_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                agent_id TEXT NOT NULL DEFAULT 'default_agent', task TEXT NOT NULL,
                search_keys TEXT NOT NULL DEFAULT '[]', solution TEXT NOT NULL,
                boundary_envs TEXT NOT NULL DEFAULT '', workspace_id TEXT,
                branch TEXT, session_id TEXT, files TEXT NOT NULL DEFAULT '[]',
                source_fact_ids TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0.7,
                source TEXT NOT NULL DEFAULT 'default',
                extraction_source TEXT NOT NULL DEFAULT 'auto_extract',
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE working_fact (
                fact_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                agent_id TEXT NOT NULL DEFAULT 'default_agent', session_id TEXT,
                workspace_id TEXT, branch TEXT, source TEXT NOT NULL DEFAULT 'default',
                statement TEXT NOT NULL, files TEXT NOT NULL DEFAULT '[]',
                subject TEXT NOT NULL DEFAULT '', details TEXT NOT NULL DEFAULT '',
                fact_link TEXT, derived_experience_ids TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'active',
                fact_chain_summary TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
            );
            """
        )
        database.execute(
            "INSERT INTO coding_memory_meta "
            "(experience_id,user_id,agent_id,task,solution,source,created_at,updated_at) "
            "VALUES ('sdk-1','local','scope','remember','remember','default','now','now')"
        )

    result = repair_sdk_metadata_from_bundle(
        data_dir=data_dir,
        database_path=store.database_path,
        now=datetime(2026, 10, 25, tzinfo=UTC),
    )
    metadata = collection.get(ids=["sdk-1"], include=["metadatas"])["metadatas"][0]
    assert result.vector_rows == 1
    assert result.coding_rows == 1
    assert metadata["source"] == "workbuddy"
    assert datetime.fromtimestamp(metadata["memory_at"], tz=UTC) == datetime(
        2026, 10, 17, tzinfo=UTC
    )
    assert datetime.fromtimestamp(metadata["gmt_created"], tz=UTC) == datetime(
        2026, 10, 18, tzinfo=UTC
    )
    with sqlite3.connect(coding) as database:
        source, memory_at, created_at = database.execute(
            "SELECT source,memory_at,created_at FROM coding_memory_meta WHERE experience_id='sdk-1'"
        ).fetchone()
    assert source == "workbuddy"
    assert datetime.fromtimestamp(memory_at, tz=UTC) == datetime(2026, 10, 17, tzinfo=UTC)
    assert created_at.startswith("2026-10-18T00:00:00")

    repeated = repair_sdk_metadata_from_bundle(
        data_dir=data_dir,
        database_path=store.database_path,
        now=datetime(2026, 10, 25, tzinfo=UTC),
    )
    assert repeated == result


def test_metadata_repair_reaches_memories_the_cutover_backfilled_under_legacy_ids(store):
    """The backfill wrote governed memories with no source; their evidence links them."""
    data_dir = store.database_path.parent
    migration_id = "sdk-authority-v2-backfill"
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "UPDATE memory_authority_state SET authority='sdk',migration_id=? WHERE singleton=1",
            (migration_id,),
        )
    # A v1 receipt from the earlier, narrower repair must not block this one.
    (data_dir / f".sdk-metadata-repair-v1-{migration_id}.json").write_text("{}\n")
    # The later bundle was taken after legacy tables were retired; the earlier
    # cutover's bundle still carries them.
    for bundle, populated in (("sdk-authority-v1-old", True), (migration_id, False)):
        legacy = data_dir / "backups" / f"{bundle}-pre-cutover.sdk-bundle" / "management"
        legacy.mkdir(parents=True)
        with closing(sqlite3.connect(legacy / "one-memory.sqlite3")) as database, database:
            database.executescript(
                """
                CREATE TABLE evidence (
                    evidence_id TEXT, source_id TEXT, created_at TEXT, imported_at TEXT
                );
                """
            )
            if not populated:
                continue
            database.executescript(
                """
                CREATE TABLE governed_memories (
                    memory_id TEXT, created_at TEXT, updated_at TEXT
                );
                CREATE TABLE governed_memory_evidence (
                    memory_id TEXT, evidence_id TEXT, active INTEGER
                );
                """
            )
            database.execute(
                "INSERT INTO governed_memories VALUES ('legacy-7','2026-09-02T00:00:00+00:00',NULL)"
            )
            database.execute("INSERT INTO governed_memory_evidence VALUES ('legacy-7','e7',1)")
            database.execute(
                "INSERT INTO evidence VALUES ('e7','workbuddy','2026-09-01T00:00:00+00:00',NULL)"
            )

    from one_memory.sdk_vector_reattach import open_offline_chroma

    vector = data_dir / "hy-memory" / "governed-v2" / "vector"
    client = open_offline_chroma(vector)
    collection = client.get_or_create_collection("memory")
    imported = int(datetime(2026, 10, 20, tzinfo=UTC).timestamp())
    collection.add(
        ids=["legacy-7", "unlinked"],
        documents=["backfilled", "hash-dated"],
        embeddings=[[0.1, 0.2], [0.2, 0.1]],
        metadatas=[
            {"source": "default", "gmt_created": imported, "memory_at": imported},
            {"source": "cursor", "gmt_created": imported, "memory_at": 8_962_051_200},
        ],
    )
    client.close()

    result = repair_sdk_metadata_from_bundle(
        data_dir=data_dir,
        database_path=store.database_path,
        now=datetime(2026, 10, 25, tzinfo=UTC),
    )

    client = open_offline_chroma(vector)
    rows = client.get_collection("memory").get(ids=["legacy-7", "unlinked"], include=["metadatas"])
    client.close()
    metadata = dict(zip(rows["ids"], rows["metadatas"], strict=True))
    assert result.vector_rows == 2
    assert metadata["legacy-7"]["source"] == "workbuddy"
    assert metadata["legacy-7"]["memory_at"] == int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())
    assert metadata["legacy-7"]["gmt_created"] == int(datetime(2026, 9, 2, tzinfo=UTC).timestamp())
    assert metadata["unlinked"]["source"] == "cursor"
    assert metadata["unlinked"]["memory_at"] == imported


def test_metadata_repair_keeps_source_unknown_when_outputs_disagree(tmp_path):
    """Two legacy memories folded into one SDK memory pool their evidence."""
    from one_memory.hy_sdk_migration import _legacy_metadata

    legacy = tmp_path / "one-memory.sqlite3"
    with closing(sqlite3.connect(legacy)) as database, database:
        database.executescript(
            """
            CREATE TABLE memory_task_outputs (body TEXT);
            CREATE TABLE governed_memories (
                memory_id TEXT, created_at TEXT, updated_at TEXT
            );
            CREATE TABLE evidence (
                evidence_id TEXT, source_id TEXT, created_at TEXT, imported_at TEXT
            );
            """
        )
        database.executemany(
            "INSERT INTO governed_memories VALUES (?,?,?)",
            [
                ("legacy-1", "2026-10-18T00:00:00+00:00", None),
                ("legacy-2", "2026-10-12T00:00:00+00:00", None),
            ],
        )
        database.executemany(
            "INSERT INTO evidence VALUES (?,?,?,NULL)",
            [
                ("e1", "workbuddy", "2026-10-17T00:00:00+00:00"),
                ("e2", "cursor", "2026-10-11T00:00:00+00:00"),
            ],
        )
        database.executemany(
            "INSERT INTO memory_task_outputs VALUES (?)",
            [
                (
                    json.dumps(
                        {
                            "memory_id": legacy_id,
                            "hy_memory_id": "sdk-1",
                            "support_refs": [{"evidence_id": evidence_id}],
                        }
                    ),
                )
                for legacy_id, evidence_id in (("legacy-1", "e1"), ("legacy-2", "e2"))
            ],
        )

    repaired = _legacy_metadata(legacy, now=datetime(2026, 10, 25, tzinfo=UTC))["sdk-1"]

    assert repaired.source is None
    assert repaired.memory_at == int(datetime(2026, 10, 17, tzinfo=UTC).timestamp())
    assert repaired.created_at == int(datetime(2026, 10, 12, tzinfo=UTC).timestamp())


def _insert_evidence(store, evidence_id, *, conversation="session-1", role="user", seq=1):
    verbatim = f"text of {evidence_id}"
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "INSERT INTO evidence (evidence_id,idempotency_key,user_id,source_id,"
            "conversation_id,content_kind,role,written_by,verbatim,content_hash,"
            "revision,imported_at,updated_at,ingest_seq) "
            "VALUES (?,?,'local','cursor',?,'raw_conversation',?,?,?,?,1,"
            "'2026-10-22T00:00:00+00:00','2026-10-22T00:00:00+00:00',?)",
            (
                evidence_id,
                evidence_id,
                conversation,
                role,
                "person" if role == "user" else "agent",
                verbatim,
                content_hash(verbatim),
                seq,
            ),
        )


def _insert_task(store, task_id, *, status, invocations):
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "INSERT INTO memory_tasks "
            "(task_id,request_id,user_id,kind,input_ref,status,profile_id,"
            "profile_revision,profile_snapshot_json,runner_token,attempts,"
            "last_error_code,created_at,updated_at,duration_seconds,hy_invocation_count) "
            "VALUES (?,?,'local','extract','e-none',?,'profile',1,'{}',NULL,0,NULL,"
            "'now','now',NULL,?)",
            (task_id, f"request-{task_id}", status, invocations),
        )


def test_authority_stays_legacy_without_explicit_cutover(store):
    assert store.authority() is MemoryAuthority.LEGACY
    progress = _start(store)
    assert progress.phase == "preflight"
    assert progress.state == "running"
    assert store.authority() is MemoryAuthority.LEGACY


def test_start_resumes_an_earlier_run_but_keeps_its_bundle(store):
    first = _start(store)
    resumed = store.start(
        migration_id="m1",
        source_runtime_build="build-19",
        target_sdk_version="2.0.6",
        source_schema_version=60,
        namespace_fingerprint="other",
        backend_fingerprint="other",
        backup_bundle_id="bundle-1",
    )
    assert resumed == first

    with pytest.raises(DataPlaneMigrationError) as caught:
        store.start(
            migration_id="m1",
            source_runtime_build="build-13",
            target_sdk_version="2.0.5",
            source_schema_version=55,
            namespace_fingerprint="namespace",
            backend_fingerprint="backend",
            backup_bundle_id="bundle-2",
        )
    assert caught.value.code == "sdk_data_plane_migration_bundle_conflict"


def test_cutover_requires_the_prepared_phase(store):
    _start(store)
    commit = {
        "migration_id": "m1",
        "sdk_version": "2.0.5",
        "store_version": "1",
        "backup_bundle_id": "bundle-1",
        "reconciliation_digest": "digest",
    }
    with pytest.raises(DataPlaneMigrationError) as caught:
        store.commit_cutover(**commit)
    assert caught.value.code == "sdk_data_plane_migration_not_ready"

    store.set_phase("m1", phase=PREPARED_PHASE, state="ready")
    marker = store.commit_cutover(**commit)
    assert marker.migration_id == "m1"
    assert store.authority() is MemoryAuthority.SDK


def test_startup_cutover_freezes_pending_job_input_and_preserves_the_queue(store):
    _start(store)
    store.set_phase("m1", phase=PREPARED_PHASE, state="ready")
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
    marker = commit_sdk_cutover(
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


def test_finished_jobs_without_input_do_not_stop_the_migration(store):
    """Seen on a real home: completed jobs whose payload the queue released."""
    from one_memory.memory_server.durable_queue import REAPED_PAYLOAD_MARKER

    queue = DurableWriteQueue(store.database_path)

    def job(key: str) -> str:
        return queue.submit(
            WriteRequest(
                internal_session_id="session-1",
                source_id="cursor",
                batch_identity=BatchIdentity(
                    internal_session_id="session-1", first_round=1, last_round=1
                ),
                evidence_ids=["gone"],
                idempotency_key=key,
            )
        ).job_id

    reaped, finished = job("reaped"), job("finished")
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "UPDATE memory_write_jobs SET state='completed',payload_json=? WHERE job_id=?",
            (json.dumps({REAPED_PAYLOAD_MARKER: "digest"}), reaped),
        )
        database.execute(
            "UPDATE memory_write_jobs SET state='completed' WHERE job_id=?", (finished,)
        )

    assert prepare_legacy_write_queue_for_cutover(store.database_path) == (0, 0)

    job("pending-without-input")
    with pytest.raises(DataPlaneMigrationError, match="write_job_input_missing"):
        prepare_legacy_write_queue_for_cutover(store.database_path)


def _chroma(home: Path):
    from one_memory.sdk_vector_reattach import open_offline_chroma

    return open_offline_chroma(home / "hy-memory" / "governed-v2" / "vector")


def _fill(client, name: str, ids: list[str], *, text: str = "old") -> None:
    collection = client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})
    collection.upsert(
        ids=ids,
        embeddings=[[float(index + 1)] * 4 for index, _ in enumerate(ids)],
        documents=[f"{text} {identifier}" for identifier in ids],
        metadatas=[{"layer": "l2_fact"} for _ in ids],
    )


def test_legacy_vectors_are_renamed_and_newer_writes_are_kept(tmp_path) -> None:
    """Build 20: 3317 memories in `one_memory_*`, the Runtime reading an empty `memhub_*`."""
    from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot, vector_collection_bases
    from one_memory.sdk_vector_reattach import (
        legacy_vector_collections,
        reattach_legacy_vectors,
    )

    profile = builtin_retrieval_snapshot()
    current, legacy = vector_collection_bases(profile)
    client = _chroma(tmp_path)
    _fill(client, f"{legacy[0]}_4", ["a", "b", "both"])
    _fill(client, f"{legacy[0]}_coding_keys_cv_4", ["k1"])
    _fill(client, f"{current}_4", ["new", "both"], text="newer")
    del client

    assert len(legacy_vector_collections(tmp_path, profile)) == 2
    done = reattach_legacy_vectors(tmp_path, profile)

    assert {(item.current, item.final_count) for item in done} == {
        (f"{current}_4", 4),
        (f"{current}_coding_keys_cv_4", 1),
    }
    assert legacy_vector_collections(tmp_path, profile) == ()
    client = _chroma(tmp_path)
    assert sorted(collection.name for collection in client.list_collections()) == [
        f"{current}_4",
        f"{current}_coding_keys_cv_4",
    ]
    chat = client.get_collection(f"{current}_4").get(ids=["both"], include=["documents"])
    assert chat["documents"] == ["newer both"]
    assert reattach_legacy_vectors(tmp_path, profile) == ()


def test_the_readback_can_open_the_store_right_after_reattach(tmp_path, monkeypatch) -> None:
    """The 1.0.4 upgrade failed its first launch at `verify.readback`.

    Reattach opened Chroma with `anonymized_telemetry=False` and never closed
    it. The SDK then opened the same directory in the same process with
    default settings, and Chroma refused: "An instance of Chroma already
    exists ... with different settings". The second launch only passed because
    nothing was left to reattach.
    """
    import chromadb

    from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot, vector_collection_bases
    from one_memory.sdk_vector_reattach import reattach_legacy_vectors

    profile = builtin_retrieval_snapshot()
    current, legacy = vector_collection_bases(profile)
    seeded = _chroma(tmp_path)
    _fill(seeded, f"{legacy[0]}_4", ["a", "b"])
    seeded.close()
    # The migration process has not configured the SDK environment yet.
    monkeypatch.delenv("ANONYMIZED_TELEMETRY", raising=False)

    assert len(reattach_legacy_vectors(tmp_path, profile)) == 1

    # What the SDK does next, in the same process: no settings of its own.
    sdk = chromadb.PersistentClient(path=str(tmp_path / "hy-memory" / "governed-v2" / "vector"))
    try:
        assert sdk.get_collection(f"{current}_4").count() == 2
    finally:
        sdk.close()


def _confirmed(store, *memory_ids: str, kind: str = "chat") -> None:
    with main_database(store.database_path).write() as database, database:
        for memory_id in memory_ids:
            database.execute(
                "INSERT INTO governed_memories (memory_id,user_id,kind,status,content,"
                "scopes_json,revision,created_at,updated_at) "
                "VALUES (?,'local',?,'confirmed','x','[]',1,'now','now')",
                (memory_id, kind),
            )


class _ReadingClient:
    """Public-API shaped SDK client that holds only `readable` ids."""

    def __init__(self, readable: set[str]) -> None:
        self.readable = readable

    def list_memories(self, *, user_id, statuses, limit, offset, order):
        del user_id, statuses, order
        ids = sorted(self.readable)
        rows = [{"memory_id": memory_id} for memory_id in ids[offset : offset + limit]]
        return {"vdb": {"memories": rows, "total": len(ids)}}

    def get(self, memory_id):
        return {"memory_id": memory_id} if memory_id in self.readable else None


def _reading(readable: set[str]):
    from contextlib import contextmanager

    @contextmanager
    def open_client(_profile):
        yield _ReadingClient(readable)

    return open_client


@pytest.fixture
def active_profile(monkeypatch):
    from one_memory.hy_sdk_runtime import builtin_retrieval_snapshot

    profile = builtin_retrieval_snapshot()
    monkeypatch.setattr("one_memory.sdk_migration_runner.read_active_profile", lambda _db: profile)
    monkeypatch.setattr("one_memory.sdk_migration_verify.read_active_profile", lambda _db: profile)
    return profile


def test_cutover_waits_until_the_new_runtime_reads_every_chat_memory(store, active_profile):
    """Build 20 reported success on 37 of 3317. Now the legacy tables stay."""
    from one_memory.sdk_migration_verify import MemoryReadbackFailed

    _confirmed(store, "m1", "m2", "m3")
    home = store.database_path.parent

    with pytest.raises(MemoryReadbackFailed) as caught:
        run_startup_migration(
            data_dir=home,
            runtime_build="21",
            sdk_version="2.0.5",
            namespace="governed-v2",
            open_client=_reading({"m1"}),
        )

    assert caught.value.code == "sdk_migration_memory_unreadable"
    assert caught.value.report.as_document() == {
        "chat_expected": 3,
        "chat_readable": 1,
        "productivity_expected": 0,
        "productivity_readable": 0,
        "passed": False,
    }
    assert store.authority() is MemoryAuthority.LEGACY
    assert "governed_memories" in legacy_tables_present(store.database_path)

    result = run_startup_migration(
        data_dir=home,
        runtime_build="21",
        sdk_version="2.0.5",
        namespace="governed-v2",
        open_client=_reading({"m1", "m2", "m3"}),
    )

    assert result.state == "cutover"
    assert result.readback is not None and result.readback.passed
    assert store.authority() is MemoryAuthority.SDK


def test_sdk_migrate_repairs_metadata_after_cutover_and_writes_the_receipt(store, active_profile):
    """76/76 memories showed source=default after an upgrade whose repair failed.

    The repair now runs here, offline, right after the cutover commits; Core's
    startup finds the receipt and does nothing.
    """
    _confirmed(store, "m1")
    home = store.database_path.parent

    result = run_startup_migration(
        data_dir=home,
        runtime_build="21",
        sdk_version="2.0.5",
        namespace="governed-v2",
        open_client=_reading({"m1"}),
    )

    assert result.state == "cutover"
    receipt = home / f".sdk-metadata-repair-v2-{result.migration_id}.json"
    assert receipt.is_file()
    assert json.loads(receipt.read_text())["migration_id"] == result.migration_id


def test_retired_coding_experiences_do_not_block_but_an_empty_store_does(store, active_profile):
    _confirmed(store, "chat-1")
    _confirmed(store, "exp-kept", "exp-retired", kind="coding")
    home = store.database_path.parent
    from one_memory.sdk_migration_verify import MemoryReadbackFailed

    with pytest.raises(MemoryReadbackFailed):
        run_startup_migration(
            data_dir=home,
            runtime_build="21",
            sdk_version="2.0.5",
            namespace="governed-v2",
            open_client=_reading({"chat-1"}),
        )
    result = run_startup_migration(
        data_dir=home,
        runtime_build="21",
        sdk_version="2.0.5",
        namespace="governed-v2",
        open_client=_reading({"chat-1", "exp-kept"}),
    )
    assert result.readback is not None
    assert (result.readback.productivity_expected, result.readback.productivity_readable) == (2, 1)


def test_memories_without_a_model_profile_keep_legacy_authority(store, monkeypatch):
    monkeypatch.setattr("one_memory.sdk_migration_runner.read_active_profile", lambda _db: None)
    _confirmed(store, "m1")

    with pytest.raises(DataPlaneMigrationError, match="sdk_migration_model_profile_required"):
        run_startup_migration(
            data_dir=store.database_path.parent,
            runtime_build="21",
            sdk_version="2.0.5",
            namespace="governed-v2",
        )
    assert store.authority() is MemoryAuthority.LEGACY


def test_a_build_20_home_gets_its_vectors_back_and_is_checked_against_its_backup(
    store, active_profile, monkeypatch
):
    from one_memory.hy_sdk_runtime import vector_collection_bases

    home = store.database_path.parent
    current, legacy_bases = vector_collection_bases(active_profile)
    client = _chroma(home)
    _fill(client, f"{legacy_bases[0]}_512", ["m1", "m2"])
    _fill(client, f"{current}_512", ["after-upgrade"], text="newer")
    del client
    _confirmed(store, "m1", "m2")
    first = run_startup_migration(
        data_dir=home,
        runtime_build="20",
        sdk_version="2.0.5",
        namespace="governed-v2",
        open_client=_reading({"m1", "m2"}),
    )
    assert first.state == "cutover"
    # Reproduce build 20: cut over while the legacy collection stayed stranded.
    client = _chroma(home)
    client.get_collection(f"{current}_512").modify(name=f"{legacy_bases[0]}_512")
    _fill(client, f"{current}_512", ["after-upgrade"], text="newer")
    del client
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=home))
    status = io.StringIO()
    assert cli.run(["sdk-migration-status", "--json"], output=status) == 0
    assert json.loads(status.getvalue())["state"] == "required"

    repaired = run_startup_migration(
        data_dir=home,
        runtime_build="21",
        sdk_version="2.0.5",
        namespace="governed-v2",
        open_client=_reading({"m1", "m2", "after-upgrade"}),
    )

    assert repaired.state == "complete"
    assert [(item.current, item.final_count) for item in repaired.reattached] == [
        (f"{current}_512", 3)
    ]
    assert repaired.readback is not None and repaired.readback.passed
    assert repaired.readback.chat_expected == 2
    status = io.StringIO()
    assert cli.run(["sdk-migration-status", "--json"], output=status) == 0
    assert json.loads(status.getvalue())["state"] == "not_required"


def test_pending_dispatch_becomes_one_write_job_per_conversation(store):
    _insert_evidence(store, "a1", conversation="a", seq=1)
    _insert_evidence(store, "a2", conversation="a", role="assistant", seq=2)
    _insert_evidence(store, "b1", conversation="b", seq=3)
    with main_database(store.database_path).write() as database, database:
        for evidence_id in ("a1", "a2", "b1"):
            database.execute(
                "INSERT INTO evidence_pending_memory_dispatch "
                "(user_id,source_id,file_key,evidence_id) VALUES ('local','cursor','f',?)",
                (evidence_id,),
            )

    assert move_pending_dispatches(store.database_path) == 2
    assert move_pending_dispatches(store.database_path) == 2

    with main_database(store.database_path).read() as database:
        payloads = [
            json.loads(row[0])
            for row in database.execute("SELECT payload_json FROM memory_write_jobs")
        ]
    assert sorted(
        [record["evidence_id"] for record in payload["input_records"]] for payload in payloads
    ) == [["a1", "a2"], ["b1"]]


def test_legacy_work_that_may_have_reached_the_sdk_does_not_block_cutover(tmp_path, store):
    """Build 19 stopped here forever: a blocker no startup could ever clear."""
    _insert_task(store, "sent", status="unknown", invocations=1)
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "INSERT INTO governed_memories (memory_id,user_id,kind,status,content,"
            "scopes_json,revision,created_at,updated_at) "
            "VALUES ('g1','local','chat','confirmed','x','[]',1,'now','now')"
        )
        database.execute(
            "INSERT INTO memory_deletions (memory_id,status,updated_at) "
            "VALUES ('g1','pending','now')"
        )
    with main_database(store.database_path).write() as database, database:
        database.execute("INSERT INTO matters(matter_id,version,needs_update) VALUES ('m1',1,0)")
    assert abandoned_legacy_work(store.database_path) == {"invoked_tasks": 1, "deletions": 1}

    _start(store)
    store.set_phase("m1", phase=PREPARED_PHASE, state="ready")
    create_sdk_migration_bundle(
        data_dir=store.database_path.parent,
        bundle_id="bundle-1",
        runtime_build="build-13",
        runtime_stopped=True,
    )
    commit_sdk_cutover(
        data_dir=store.database_path.parent,
        database_path=store.database_path,
        migration_id="m1",
        bundle_id="bundle-1",
        sdk_version="2.0.5",
    )
    assert store.authority() is MemoryAuthority.SDK


def _protective(home: Path) -> Path:
    return home / "hy-memory" / ".governed-v2.pre-migration-0123456789abcdef"


def test_interrupted_swap_before_authority_restores_the_original_store(tmp_path, store):
    home = store.database_path.parent
    live = home / "hy-memory" / "governed-v2"
    live.mkdir(parents=True)
    (live / "store.txt").write_text("staged", encoding="utf-8")
    _protective(home).mkdir()
    (_protective(home) / "store.txt").write_text("original", encoding="utf-8")
    (home / ".sdk-migration" / "token").mkdir(parents=True)

    recover_abandoned_staging(
        data_dir=home,
        database_path=store.database_path,
        namespace="governed-v2",
    )

    assert (live / "store.txt").read_text(encoding="utf-8") == "original"
    assert not _protective(home).exists()
    assert not (home / ".sdk-migration").exists()


def test_stale_protective_root_after_authority_is_discarded(tmp_path, store):
    home = store.database_path.parent
    live = home / "hy-memory" / "governed-v2"
    live.mkdir(parents=True)
    (live / "store.txt").write_text("live", encoding="utf-8")
    _protective(home).mkdir()
    _start(store)
    store.set_phase("m1", phase=PREPARED_PHASE, state="ready")
    store.commit_cutover(
        migration_id="m1",
        sdk_version="2.0.5",
        store_version="1",
        backup_bundle_id="bundle-1",
        reconciliation_digest="digest",
    )

    recover_abandoned_staging(
        data_dir=home,
        database_path=store.database_path,
        namespace="governed-v2",
    )

    assert (live / "store.txt").read_text(encoding="utf-8") == "live"
    assert not _protective(home).exists()


def test_host_capture_is_fenced_for_the_whole_migration(store):
    from one_memory.live_capture import sdk_migration_capture_fenced

    home = store.database_path.parent
    fenced: dict[str, bool] = {}

    result = run_startup_migration(
        data_dir=home,
        runtime_build="20",
        sdk_version="2.0.5",
        namespace="governed-v2",
        progress=lambda step: fenced.__setitem__(step, sdk_migration_capture_fenced(home)),
    )

    assert result.state == "cutover"
    assert fenced == {"bundle": True, "prepare": True, "cutover": True, "cleanup": True}
    assert not sdk_migration_capture_fenced(home)


def test_a_home_blocked_by_build_19_finishes_on_the_next_startup(tmp_path, monkeypatch, store):
    """The quarantined ledger, its staging copy and pending queue work all resolve."""
    home = store.database_path.parent
    live = home / "hy-memory" / "governed-v2"
    live.mkdir(parents=True)
    (live / "coding.sqlite3").write_text("sdk data", encoding="utf-8")
    staging = home / ".sdk-migration" / "token" / "hy-memory" / "governed-v2"
    staging.mkdir(parents=True)
    store.start(
        migration_id="sdk-authority-v2-73b3561be69371e0",
        source_runtime_build="19",
        target_sdk_version="2.0.6",
        source_schema_version=60,
        namespace_fingerprint="old",
        backend_fingerprint="old-with-embedding",
        backup_bundle_id="sdk-authority-v2-73b3561be69371e0-pre-cutover",
    )
    store.set_phase(
        "sdk-authority-v2-73b3561be69371e0",
        phase="review_required",
        state="blocked",
    )
    with main_database(store.database_path).write() as database, database:
        database.execute(
            "INSERT INTO sdk_data_plane_migration_items (migration_id,item_key,"
            "source_kind,source_id,source_hash,operation_key,state,error_code,updated_at) "
            "VALUES ('sdk-authority-v2-73b3561be69371e0','memory:exp-1',"
            "'memory:quarantine','exp-1','hash','op','quarantine',"
            "'mapped_productivity_memory_missing','now')"
        )
    _insert_evidence(store, "e1")
    DurableWriteQueue(store.database_path).submit(
        WriteRequest(
            internal_session_id="session-1",
            source_id="cursor",
            batch_identity=BatchIdentity(
                internal_session_id="session-1", first_round=1, last_round=1
            ),
            evidence_ids=["e1"],
            idempotency_key="pending-job",
        )
    )
    monkeypatch.setattr(cli, "load_settings", lambda: Settings(_env_file=None, data_dir=home))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )
    output = io.StringIO()
    code = cli.run(["sdk-migrate", "--runtime-build", "20", "--yes", "--json"], output=output)
    result = json.loads(output.getvalue().splitlines()[-1])

    assert (code, result["state"]) == (0, "cutover"), output.getvalue()
    assert result["migration_id"] == "sdk-authority-v2-73b3561be69371e0"
    assert result["bundle_id"] == "sdk-authority-v2-73b3561be69371e0-pre-cutover"
    assert result["frozen_jobs"] == 1
    assert store.authority() is MemoryAuthority.SDK
    assert legacy_tables_present(store.database_path) == ()
    with main_database(store.database_path).read() as database:
        assert not database.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'evidence%'"
        ).fetchall()
    assert (live / "coding.sqlite3").read_text(encoding="utf-8") == "sdk data"
    assert not (home / ".sdk-migration").exists()
    queue = DurableWriteQueue(store.database_path, sdk_authority=True)
    job = queue.get("pending-job")
    assert job is not None and job.state == "pending"
    assert queue.payload_for(job.job_id).input_records[0].verbatim == "text of e1"


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


# The UI asks which memory data plane is authoritative and must get the
# answer the API gates use.


def _app_settings(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        source_home=tmp_path,
        api_token=SecretStr("authority-test"),
        imports_enabled=False,
        automatic_memory_processing=False,
        live_sync_enabled=False,
    )


async def _authority(tmp_path) -> tuple[dict[str, object], str]:
    """What a starting Core reads as authority, and the gate it then holds."""
    app = create_app(_app_settings(tmp_path), secret_store=InMemorySecretStore())
    async with app.router.lifespan_context(app):
        authority, migration_id = app.state.sdk_data_plane_migrations.authority_state()
        return (
            {"authority": authority.value, "migration_id": migration_id},
            app.state.memory_authority.value,
        )


@pytest.mark.integration
@pytest.mark.fresh_sdk
def test_a_fresh_home_reports_sdk_authority(tmp_path) -> None:
    document, gate = asyncio.run(_authority(tmp_path))

    assert document == {"authority": "sdk", "migration_id": None}
    assert gate == "sdk"


@pytest.mark.integration
def test_a_migrated_home_still_reports_sdk_after_its_receipt_is_dropped(
    tmp_path, monkeypatch
) -> None:
    """Build 20: the cleanup dropped the cutover receipt, the UI fell back to legacy."""
    data_dir = tmp_path / "data"
    EvidenceStore(data_dir / "one-memory.sqlite3")
    monkeypatch.setattr(cli, "load_settings", lambda: _app_settings(tmp_path))
    monkeypatch.setattr(
        cli,
        "inspect_runtime",
        lambda _data_dir: RuntimeStatus("stopped", "runtime_receipt_missing"),
    )
    output = io.StringIO()
    assert cli.run(["sdk-migrate", "--runtime-build", "21", "--yes", "--json"], output=output) == 0
    migration_id = json.loads(output.getvalue().splitlines()[-1])["migration_id"]
    assert legacy_tables_present(data_dir / "one-memory.sqlite3") == ()

    document, gate = asyncio.run(_authority(tmp_path))

    assert document == {"authority": "sdk", "migration_id": migration_id}
    assert gate == "sdk"


@pytest.mark.integration
def test_a_legacy_home_reports_legacy_authority(tmp_path) -> None:
    document, gate = asyncio.run(_authority(tmp_path))

    assert document == {"authority": "legacy", "migration_id": None}
    assert gate == "legacy"


@pytest.mark.integration
@pytest.mark.fresh_sdk
def test_the_sdk_memory_map_draws_sdk_memories_without_legacy_tables(tmp_path) -> None:
    """Build 21: the memory map had no SDK read and waited forever."""
    from one_memory.hy_graph_projection import HyGraphEdge, HyGraphNode, HyGraphSnapshot

    rows = [
        {
            "memory_id": memory_id,
            "content": f"memory {memory_id}",
            "layer": "l2_fact",
            "status": "active",
            "is_latest": True,
            "user_id": "local",
            "agent_id": "all-apps",
            "session_id": "s1",
            "source": "cursor",
        }
        for memory_id in ("a", "b")
    ] + [
        {
            "memory_id": memory_id,
            "content": f"backfilled {memory_id}",
            "layer": "l2_fact",
            "status": "active",
            "user_id": "local",
            "agent_id": "one-memory",
            "session_id": "default_session",
            "source": "default",
        }
        for memory_id in ("c", "d")
    ]
    listed = []

    class Client:
        def list_memories(self, **kwargs):
            listed.append(kwargs)
            return {"vdb": {"memories": rows, "total": 7}}

    class Handle:
        async def client(self):
            return Client()

        async def close(self):
            return None

    async def scenario():
        app = create_app(_app_settings(tmp_path), secret_store=InMemorySecretStore())
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer authority-test"},
            ) as client,
        ):
            original = app.state.sdk_client_handle
            app.state.sdk_client_handle = Handle()

            async def graph_snapshot(*, limit):
                del limit
                return HyGraphSnapshot(
                    nodes=(HyGraphNode(node_id="t1", content="topic", layer="TOPIC"),),
                    edges=(
                        HyGraphEdge(
                            source_node_id="t1", target_node_id="a", relation="derived_from"
                        ),
                    ),
                )

            app.state.sdk_memory_runtime.graph_snapshot = graph_snapshot
            response = await client.get(
                "/api/v2/memories/graph",
                params={"source": "cursor", "offset": 100, "limit": 100},
            )
            app.state.sdk_client_handle = original
            assert response.status_code == 200
            return response.json()

    graph = asyncio.run(scenario())
    assert listed[0]["include_sources"] == ["cursor"]
    assert (listed[0]["offset"], listed[0]["limit"]) == (100, 100)
    assert (graph["total_memories"], graph["projected_memories"]) == (7, 4)
    assert {node["node_id"] for node in graph["nodes"]} == {
        "memory:a",
        "memory:b",
        "memory:c",
        "memory:d",
        "source:cursor",
        "hy:t1",
    }
    # "default_session" is the SDK's filler, not a conversation.
    assert [
        (edge["source_node_id"], edge["target_node_id"])
        for edge in graph["edges"]
        if edge["relation"] == "same_conversation"
    ] == [("memory:a", "memory:b")]
    assert {edge["relation"] for edge in graph["edges"]} == {
        "evidence_source",
        "same_conversation",
        "derived_from",
    }
    assert graph["hy_graph_available"] is True


def test_a_completed_sdk_write_does_not_touch_retired_evidence(store) -> None:
    """Build 21 logged RetiredAuthorityAccess after every successful write."""
    from types import SimpleNamespace

    from one_memory.imports.storage import EvidenceStore as Evidence
    from one_memory.memory_server.durable_queue import DurableWriteDrain, execute_job
    from one_memory.retired_authority import RetiredEvidenceStore

    path = store.database_path
    _insert_evidence(store, "e1")
    record = Evidence(path).get("e1")
    assert record is not None
    queue = DurableWriteQueue(path, sdk_authority=True)
    job = queue.submit(
        WriteRequest(
            internal_session_id="s",
            source_id="cursor",
            batch_identity=BatchIdentity(internal_session_id="s", first_round=1, last_round=1),
            evidence_ids=["e1"],
            input_records=(record,),
            idempotency_key="k",
        )
    )
    DurableWriteDrain(queue, execute=lambda _job: asyncio.sleep(0)).claim(limit=1)

    class Supervisor:
        async def process_sdk_job(self, records, *, request_id, before_hy_invocation):
            del records, request_id
            await before_hy_invocation()
            return SimpleNamespace(raw_ids=("raw-1",), memory_ids=("m-1",))

    asyncio.run(
        execute_job(
            job.job_id,
            queue=queue,
            supervisor=Supervisor(),
            evidence=RetiredEvidenceStore(path),
        )
    )
    assert queue.get("k").state == "completed"
