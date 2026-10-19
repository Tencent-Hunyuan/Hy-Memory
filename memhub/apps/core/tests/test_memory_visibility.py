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


@pytest.mark.parametrize("visibility_mode", ["exact", "derived"])
def test_hy_revise_and_repeated_link_reuse_the_same_evidence(library, monkeypatch, visibility_mode):
    """The outer evidence UPSERT must not override the trigger's duplicate handling."""
    database, evidence, _tasks, governance, _profile = library
    monkeypatch.setenv("ONE_MEMORY_READ_VISIBILITY", visibility_mode)
    startup(database)
    record = evidence.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="trigger-regression",
            message_id="trigger-regression",
            correlation_id="trigger-regression",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="用户住在杭州。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    created = governance.write_from_hy(
        HyCreate(
            draft=MemoryDraft(
                user_id="local",
                kind=MemoryKind.CHAT,
                content="用户住在杭州。",
                scopes=("all-apps",),
                evidence_ids=(record.evidence_id,),
            ),
            request_id="trigger-create",
            memory_id="trigger-memory",
        ),
        user_id="local",
        derivation=governance.hy_derivation((record.evidence_id,)),
    )
    revision = HyRevise(
        memory_id=created.memory_id,
        request_id="trigger-revise",
        expected_revision=created.revision,
        content="用户住在杭州并在那里工作。",
        evidence_ids=(record.evidence_id,),
    )
    revised = governance.write_from_hy(
        revision,
        user_id="local",
        derivation=governance.hy_derivation((record.evidence_id,)),
    )
    replayed = governance.write_from_hy(
        revision,
        user_id="local",
        derivation=governance.hy_derivation((record.evidence_id,)),
    )
    assert replayed == revised
    link = HyLink(memory_id=created.memory_id, evidence_id=record.evidence_id)
    for _ in range(2):
        governance.write_from_hy(
            link,
            user_id="local",
            derivation=governance.hy_derivation((record.evidence_id,)),
        )

    current = governance.get(created.memory_id, user_id="local")
    assert current is not None
    assert current.content == "用户住在杭州并在那里工作。"
    assert current.revision == created.revision + 1
    with main_database_read(database) as db:
        assert db.execute(
            "SELECT memory_id, COUNT(*) FROM memory_visibility_dirty GROUP BY memory_id"
        ).fetchall() == [(created.memory_id, 1)]

    if visibility_mode == "derived":
        monkeypatch.setenv("ONE_MEMORY_READ_VISIBILITY", "exact")
        exact = {memory.memory_id for memory in SourceReadAccess(database).filter([current])}
        with main_database_write(database) as db:
            assert recompute(db, [created.memory_id], readable_sql=_MEMORY_READABLE) == 1
            db.commit()
        monkeypatch.setenv("ONE_MEMORY_READ_VISIBILITY", "derived")
        derived = {memory.memory_id for memory in SourceReadAccess(database).filter([current])}
        assert derived == exact
        with main_database_read(database) as db:
            assert created.memory_id not in pending(db)


def test_startup_atomically_replaces_the_legacy_link_update_trigger(tmp_path, monkeypatch):
    import one_memory.memory_visibility as visibility_module
    from one_memory.schema_gate import guard_schema_version

    database = tmp_path / "one-memory.sqlite3"
    expected_version = guard_schema_version(database)
    evidence = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    record = evidence.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="legacy-trigger",
            message_id="legacy-trigger",
            correlation_id="legacy-trigger",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="升级失败不能改变这条原文。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    memory = as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content="升级失败不能改变这条记忆。",
            scopes=("all-apps",),
            evidence_ids=(record.evidence_id,),
        ),
        request_id="legacy-trigger-memory",
    )
    startup(database)
    with sqlite3.connect(database) as db:
        db.execute("DROP TRIGGER memory_visibility_dirty_on_link_update")
        db.executescript(
            """
            CREATE TRIGGER memory_visibility_dirty_on_link_update
            AFTER UPDATE ON governed_memory_evidence
            BEGIN
                INSERT OR IGNORE INTO memory_visibility_dirty (memory_id)
                VALUES (NEW.memory_id);
                INSERT OR IGNORE INTO memory_visibility_dirty (memory_id)
                VALUES (OLD.memory_id);
            END;
            """
        )
        db.execute(
            "INSERT INTO memory_visibility (memory_id, visible, reason, computed_at) "
            "VALUES (?, 1, 'ok', '2026-10-19T00:00:00+00:00')",
            (memory.memory_id,),
        )
        db.execute(
            "INSERT INTO memory_visibility_dirty (memory_id) VALUES (?)",
            (memory.memory_id,),
        )
        legacy_trigger = db.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='trigger' AND name='memory_visibility_dirty_on_link_update'"
        ).fetchone()[0]
        before = tuple(db.iterdump())

    real_trigger_sql = visibility_module._trigger_sql

    def fail_after_drop() -> list[tuple[str, str]]:
        statements = real_trigger_sql()
        return [
            (table, "CREATE TRIGGER")
            if "CREATE TRIGGER IF NOT EXISTS memory_visibility_dirty_on_link_update" in statement
            else (table, statement)
            for table, statement in statements
        ]

    with monkeypatch.context() as failure:
        failure.setattr(visibility_module, "_trigger_sql", fail_after_drop)
        with pytest.raises(sqlite3.OperationalError):
            startup(database)

    with sqlite3.connect(database) as db:
        assert tuple(db.iterdump()) == before
        assert (
            db.execute(
                "SELECT sql FROM sqlite_master "
                "WHERE type='trigger' AND name='memory_visibility_dirty_on_link_update'"
            ).fetchone()[0]
            == legacy_trigger
        )
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == (
            expected_version
        )

    startup(database)
    startup(database)

    with sqlite3.connect(database) as db:
        trigger_sql = db.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type='trigger' AND name='memory_visibility_dirty_on_link_update'"
        ).fetchone()[0]
        assert trigger_sql.count("ON CONFLICT(memory_id) DO NOTHING") == 2
        assert "INSERT OR IGNORE" not in trigger_sql
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == (
            expected_version
        )
        assert db.execute("SELECT * FROM memory_visibility").fetchall() == [
            (memory.memory_id, 1, "ok", "2026-10-19T00:00:00+00:00")
        ]
        assert db.execute("SELECT * FROM memory_visibility_dirty").fetchall() == [
            (memory.memory_id,)
        ]


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
