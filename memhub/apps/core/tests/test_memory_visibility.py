"""The stored visibility conclusion must agree with the exact check."""

import asyncio
import sqlite3

import pytest

from one_memory.imports.grants import GRANT_SCHEMA
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import (
    HyCreate,
    HyLink,
    HyRevise,
    MemoryDraft,
    MemoryGovernanceStore,
    MemoryKind,
    MemoryMutationConflictError,
)
from one_memory.memory_tasks import (
    MemoryTaskOutput,
    MemoryTaskStore,
    evidence_ref,
)
from one_memory.memory_visibility import (
    ensure_tables,
    install_triggers,
    pending,
    recompute,
    startup,
)
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfileInput,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore
from one_memory.source_read_access import _MEMORY_READABLE, SourceReadAccess
from tests.person_door import as_person


@pytest.fixture
def library(tmp_path):
    database = tmp_path / "one-memory.sqlite3"
    with sqlite3.connect(database) as setup:
        setup.executescript(GRANT_SCHEMA)
    secrets = InMemorySecretStore()
    evidence = EvidenceStore(database)
    profiles = ModelProfileStore(database)
    tasks = MemoryTaskStore(database)
    governance = MemoryGovernanceStore(database)
    profile = profiles.create(
        ModelProfileInput(
            name="Local",
            provider_kind=ProviderKind.OLLAMA,
            model="hy-bot-phi4",
            embedding_model="nomic-embed-text",
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
    profile = profiles.activate(profile.profile_id, secrets)
    return database, evidence, tasks, governance, profile


def _memory(evidence, governance, tasks, content, source_id, index):
    record = evidence.put(
        EvidenceInput(
            source_id=source_id,
            conversation_id=f"conversation-{index}",
            message_id=f"m-{index}",
            correlation_id=f"m-{index}",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim=content,
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    memory = as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content=content,
            scopes=("all-apps",),
            evidence_ids=(record.evidence_id,),
        ),
        request_id=f"create-{index}",
    )
    tasks.save_output(
        MemoryTaskOutput(
            task_id=f"task-{index}",
            memory_id=memory.memory_id,
            memory_revision=memory.revision,
            hy_memory_id=f"hy-{index}",
            owner="user",
            layer="L2_FACT",
            engine_version="fixture",
            support_refs=(evidence_ref(record),),
        )
    )
    return memory, record


def test_the_stored_conclusion_matches_the_exact_check(library):
    """D93 A: the cache must not have its own opinion.

    Recomputation is handed the read path's own predicate, so what is stored
    is the same answer the reader would have computed — only earlier.
    """
    database, evidence, tasks, governance, profile = library
    memories = [
        _memory(evidence, governance, tasks, f"第 {index} 条真实对话内容。", "cursor", index)[0]
        for index in range(4)
    ]

    with main_database_write(database) as db:
        ensure_tables(db)
        recompute(db, [m.memory_id for m in memories], readable_sql=_MEMORY_READABLE)
        # The write connection rolls back on leaving, so the batch commits.
        db.commit()

    # What the exact check says right now, for the same memories.
    exact = {m.memory_id for m in SourceReadAccess(database).filter(memories)}
    with main_database_read(database) as db:
        cursor = db.cursor()
        cursor.row_factory = sqlite3.Row
        stored = {
            str(row["memory_id"])
            for row in cursor.execute("SELECT memory_id FROM memory_visibility WHERE visible=1")
        }

    assert stored == exact


def test_a_write_marks_the_affected_memory_dirty(library):
    """Triggers rather than calls at each site: a write cannot be missed."""
    database, evidence, tasks, governance, profile = library
    memory, record = _memory(evidence, governance, tasks, "一条真实对话内容。", "cursor", 0)

    with main_database_write(database) as db:
        ensure_tables(db)
        install_triggers(db)
        db.commit()

    # A new version of the cited evidence invalidates the stored conclusion.
    evidence.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation-0",
            message_id="m-0",
            correlation_id="m-0",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="一条真实对话内容（改过了）。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    )

    with main_database_read(database) as db:
        assert memory.memory_id in pending(db)


def main_database_write(path):
    from one_memory.database_access import main_database

    return main_database(path).write()


def main_database_read(path):
    from one_memory.database_access import main_database

    return main_database(path).read()


@pytest.mark.parametrize("legacy_triggers", [False, True])
def test_hy_revision_and_link_upserts_survive_visibility_startup(library, legacy_triggers):
    """Real HY writeback must tolerate an already-dirty citation on new and old homes."""
    from one_memory.database_access import close_main_databases

    database, evidence, _tasks, governance, _profile = library
    records = [
        evidence.put(
            EvidenceInput(
                source_id="codex",
                conversation_id="visibility-upsert",
                message_id=f"message-{index}",
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim=f"Synthetic project fact {index}",
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        ).record
        for index in range(2)
    ]
    ids = tuple(record.evidence_id for record in records)
    startup(database)
    if legacy_triggers:
        # Existing homes retain these released definitions under CREATE IF NOT EXISTS.
        with main_database_write(database) as db:
            for name, event, values in (
                ("memory_visibility_dirty_on_link", "INSERT", ("NEW",)),
                ("memory_visibility_dirty_on_link_update", "UPDATE", ("NEW", "OLD")),
            ):
                db.execute(f"DROP TRIGGER {name}")
                body = " ".join(
                    "INSERT OR IGNORE INTO memory_visibility_dirty(memory_id) "
                    f"VALUES ({value}.memory_id);"
                    for value in values
                )
                db.execute(
                    f"CREATE TRIGGER {name} AFTER {event} ON governed_memory_evidence "
                    f"BEGIN {body} END"
                )
    close_main_databases()
    startup(database)
    current = governance.write_from_hy(
        HyCreate(
            draft=MemoryDraft(
                user_id="local",
                kind=MemoryKind.WORKING,
                content="Initial synthetic project fact",
                scopes=("all-apps",),
                evidence_ids=ids[:1],
            ),
            request_id="visibility-create",
            memory_id="visibility-upsert-memory",
        ),
        user_id="local",
        derivation=governance.hy_derivation(ids),
    )
    original = current
    for index, cited in enumerate((ids, ids, ids[1:])):
        current = governance.write_from_hy(
            HyRevise(
                memory_id=current.memory_id,
                request_id=f"visibility-revise-{index}",
                expected_revision=current.revision,
                content=f"Revised synthetic project fact {index}",
                evidence_ids=cited,
            ),
            user_id="local",
            derivation=governance.hy_derivation(ids),
        )
    governance.write_from_hy(
        HyLink(memory_id=current.memory_id, evidence_id=ids[0]),
        user_id="local",
        derivation=governance.hy_derivation(ids),
    )
    with pytest.raises(MemoryMutationConflictError):
        governance.write_from_hy(
            HyRevise(
                current.memory_id,
                "stale-visibility-revision",
                original.revision,
                "Must not commit",
                ids,
            ),
            user_id="local",
            derivation=governance.hy_derivation(ids),
        )
    assert governance.get(current.memory_id, user_id="local").content == current.content
    with main_database_read(database) as db:
        assert (
            db.execute(
                "SELECT count(*) FROM memory_visibility_dirty WHERE memory_id=?",
                (current.memory_id,),
            ).fetchone()[0]
            == 1
        )
        assert (
            db.execute(
                "SELECT content FROM governed_memory_versions WHERE memory_id=? AND revision=?",
                (original.memory_id, original.revision),
            ).fetchone()[0]
            == original.content
        )
        schema_version = db.execute("PRAGMA schema_version").fetchone()[0]
    close_main_databases()
    startup(database)
    with main_database_read(database) as db:
        assert db.execute("PRAGMA schema_version").fetchone()[0] == schema_version
    governance.write_from_hy(
        HyRevise(
            current.memory_id,
            "after-visibility-restart",
            current.revision,
            "Synthetic fact after restart",
            ids,
        ),
        user_id="local",
        derivation=governance.hy_derivation(ids),
    )


def test_link_update_marks_both_identities_without_hiding_real_conflicts():
    with sqlite3.connect(":memory:") as db:
        db.execute(
            "CREATE TABLE governed_memory_evidence (memory_id TEXT, evidence_id TEXT, "
            "active INTEGER, PRIMARY KEY(memory_id,evidence_id))"
        )
        ensure_tables(db)
        install_triggers(db)
        db.execute("INSERT INTO governed_memory_evidence VALUES ('old-memory','evidence',1)")
        db.execute("DELETE FROM memory_visibility_dirty")
        db.execute("UPDATE governed_memory_evidence SET memory_id='new-memory'")
        assert set(pending(db)) == {"old-memory", "new-memory"}
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO governed_memory_evidence VALUES ('new-memory','evidence',1)")


def test_failed_link_trigger_upgrade_keeps_the_existing_trigger(monkeypatch):
    import one_memory.memory_visibility as visibility

    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE governed_memory_evidence (memory_id TEXT, evidence_id TEXT)")
        ensure_tables(db)
        old_sql = (
            "CREATE TRIGGER memory_visibility_dirty_on_link "
            "AFTER INSERT ON governed_memory_evidence BEGIN "
            "INSERT OR IGNORE INTO memory_visibility_dirty VALUES (NEW.memory_id); END"
        )
        db.execute(old_sql)
        monkeypatch.setattr(
            visibility, "_trigger_sql", lambda: [("governed_memory_evidence", "INVALID SQL")]
        )
        with pytest.raises(sqlite3.OperationalError):
            install_triggers(db)
        assert (
            db.execute(
                "SELECT sql FROM sqlite_master WHERE name='memory_visibility_dirty_on_link'"
            ).fetchone()[0]
            == old_sql
        )


def test_a_read_uses_the_stored_conclusion_without_rechecking(library):
    """D93 A: a known conclusion is read, not recomputed.

    The point of the whole exercise. Once a memory's visibility is known and
    nothing has touched it since, deciding it again on every read is the cost
    being removed.
    """
    database, evidence, tasks, governance, profile = library
    memories = [
        _memory(evidence, governance, tasks, f"第 {index} 条真实对话内容。", "cursor", index)[0]
        for index in range(4)
    ]

    with main_database_write(database) as db:
        ensure_tables(db)
        recompute(db, [m.memory_id for m in memories], readable_sql=_MEMORY_READABLE)
        db.commit()

    access = SourceReadAccess(database)
    stored_before = len(
        [
            row
            for row in sqlite3.connect(database).execute(
                "SELECT memory_id FROM memory_visibility WHERE visible=1"
            )
        ]
    )
    readable = access.filter(memories)

    # Same answer as the exact check, and the conclusion was already there.
    assert {m.memory_id for m in readable} == {m.memory_id for m in memories} or stored_before == 0
    assert stored_before > 0


def test_a_memory_without_a_conclusion_is_still_checked_exactly(library):
    """A half-filled cache must not be read as "everything is hidden"."""
    database, evidence, tasks, governance, profile = library
    memory, _record = _memory(evidence, governance, tasks, "一条真实对话内容。", "cursor", 0)

    with main_database_write(database) as db:
        ensure_tables(db)
        db.commit()

    # Nothing has been computed for it yet.
    readable = SourceReadAccess(database).filter([memory])
    assert {m.memory_id for m in readable} == {memory.memory_id}


def test_a_known_conclusion_is_not_checked_again(library, monkeypatch):
    """The exact check is skipped, not just answered faster.

    Counting how often the freshness function is asked: with every memory's
    visibility already known, it must not be asked at all. That is the cost
    being taken off the read path.
    """
    import one_memory.source_read_access as read_access

    database, evidence, tasks, governance, profile = library
    memories = [
        _memory(evidence, governance, tasks, f"第 {index} 条真实对话内容。", "cursor", index)[0]
        for index in range(4)
    ]

    with main_database_write(database) as db:
        ensure_tables(db)
        recompute(db, [m.memory_id for m in memories], readable_sql=_MEMORY_READABLE)
        db.commit()

    asked: list[str] = []
    real = read_access.register_memory_freshness

    def counting(db):
        real(db)
        db.create_function("is_memory_evidence_current", 3, lambda m, r, u: asked.append(m) or 1)

    monkeypatch.setattr(read_access, "register_memory_freshness", counting)
    monkeypatch.setenv("ONE_MEMORY_READ_VISIBILITY", "derived")
    try:
        readable = SourceReadAccess(database).filter(memories)
    finally:
        monkeypatch.delenv("ONE_MEMORY_READ_VISIBILITY", raising=False)

    assert {m.memory_id for m in readable} == {m.memory_id for m in memories}
    assert asked == [], "the exact check ran for a memory whose visibility was known"


def test_a_batch_computes_what_is_missing_then_what_went_dirty(library):
    """The background pass: backfill first, then answer what writes marked."""
    from one_memory.api import _visibility_batch

    database, evidence, tasks, governance, profile = library
    memories = [
        _memory(evidence, governance, tasks, f"第 {index} 条真实对话内容。", "cursor", index)[0]
        for index in range(3)
    ]

    with main_database_write(database) as db:
        ensure_tables(db)
        install_triggers(db)
        db.commit()

    # Nothing known yet, so the first batch backfills.
    assert _visibility_batch(database) > 0

    with main_database_read(database) as db:
        cursor = db.cursor()
        cursor.row_factory = sqlite3.Row
        stored = {
            str(row["memory_id"])
            for row in cursor.execute("SELECT memory_id FROM memory_visibility WHERE visible=1")
        }
    assert stored == {m.memory_id for m in memories}

    # A write marks one dirty; the next batch answers it.
    evidence.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation-0",
            message_id="m-0",
            correlation_id="m-0",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="第 0 条真实对话内容（改过了）。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    )
    with main_database_read(database) as db:
        assert memories[0].memory_id in pending(db)

    assert _visibility_batch(database) > 0
    with main_database_read(database) as db:
        assert memories[0].memory_id not in pending(db)


def test_starting_the_app_creates_the_derived_tables(tmp_path):
    """Idempotent and unversioned: no migration, no version ledger change."""
    from httpx import ASGITransport, AsyncClient

    from one_memory.api import create_app
    from one_memory.config import Settings

    app = create_app(Settings(data_dir=tmp_path / "data", source_home=tmp_path, api_token="t"))
    client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")

    async def scenario() -> None:
        async with app.router.lifespan_context(app):
            await client.get("/api/v1/health")
            database = tmp_path / "data" / "one-memory.sqlite3"
            with sqlite3.connect(database) as db:
                tables = {
                    row[0]
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
            assert "memory_visibility" in tables
            assert "memory_visibility_dirty" in tables
            assert version is not None
        await client.aclose()

    asyncio.run(scenario())


def test_the_audit_notices_a_conclusion_that_no_longer_holds(library):
    """The ongoing proof: recompute a slice and compare with what is stored.

    A trigger that never fires for some change would leave a wrong
    conclusion in place silently, so this is checked in production too, not
    only in tests.
    """
    from one_memory.memory_visibility import audit

    database, evidence, tasks, governance, profile = library
    memories = [
        _memory(evidence, governance, tasks, f"第 {index} 条真实对话内容。", "cursor", index)[0]
        for index in range(3)
    ]

    with main_database_write(database) as db:
        ensure_tables(db)
        recompute(db, [m.memory_id for m in memories], readable_sql=_MEMORY_READABLE)
        db.commit()

    with main_database_read(database) as db:
        db.execute("BEGIN")
        assert audit(db, readable_sql=_MEMORY_READABLE) == 0

    # Force one conclusion to be wrong behind the cache's back.
    with main_database_write(database) as db:
        db.execute(
            "UPDATE memory_visibility SET visible=0 WHERE memory_id=?",
            (memories[0].memory_id,),
        )
        db.commit()

    with main_database_read(database) as db:
        db.execute("BEGIN")
        assert audit(db, readable_sql=_MEMORY_READABLE) >= 1


def test_granting_a_source_repeatedly_does_not_raise(library):
    """Re-granting must not fail on a memory the trigger already marked.

    The grant trigger inserts from a `SELECT`, and the same memory is matched
    again on a second grant. `INSERT OR IGNORE` does not cover a duplicate the
    select itself produces, so the write raised a UNIQUE violation and a grant
    stopped working — which two existing tests caught.
    """
    database, evidence, tasks, governance, profile = library
    memory, _record = _memory(evidence, governance, tasks, "一条真实对话内容。", "cursor", 0)

    with main_database_write(database) as db:
        ensure_tables(db)
        install_triggers(db)
        db.commit()

    from one_memory.imports.candidates import RootCandidateKind
    from one_memory.imports.grants import SourceGrantStore

    folder = database.parent / "cursor"
    folder.mkdir(exist_ok=True)
    store = SourceGrantStore(database, home=database.parent)
    # Second grant of the same source: the trigger's select still matches the
    # memory, which is already in the dirty set.
    store.grant(
        user_id="local",
        source_id="cursor",
        root=folder,
        candidate_kind=RootCandidateKind.DEFAULT,
    )
    store.grant(
        user_id="local",
        source_id="cursor",
        root=folder,
        candidate_kind=RootCandidateKind.DEFAULT,
    )

    with main_database_read(database) as db:
        assert memory.memory_id in pending(db)
