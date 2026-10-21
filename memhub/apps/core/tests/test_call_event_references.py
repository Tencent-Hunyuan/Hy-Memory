import hashlib
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from pydantic import ValidationError

from one_memory.call_event_references import (
    CallReferencesUnavailable,
    SourceRecordRef,
    resolve_call_references,
)
from one_memory.call_event_store import CallEvent, CallReference
from one_memory.database_access import close_main_databases, main_database
from one_memory.imports.grants import GRANT_SCHEMA
from one_memory.memory_governance import MemoryGovernanceStore
from one_memory.source_read_access import SourceReadAccess
from tests.person_door import as_person
from tests.test_memory_evidence_freshness import case as _freshness_case  # noqa: F401
from tests.test_memory_library import database as _library_database  # noqa: F401
from tests.test_memory_library import grant, put


@pytest.fixture
def database(_library_database):  # noqa: F811 - pytest injects the imported fixture
    return _library_database


@pytest.fixture
def fresh(_freshness_case):  # noqa: F811 - pytest injects the imported fixture
    with closing(sqlite3.connect(_freshness_case[0])) as db, db:
        db.executescript(GRANT_SCHEMA)
    return _freshness_case


def ref(kind="raw", key="e1", revision=1, source="cursor"):
    return CallReference(kind=kind, id=key, revision=revision, source_id=source)


def event(*references):
    return CallEvent(
        event_id=uuid4(),
        occurred_at=datetime.now(UTC),
        host_id="codex",
        installation_id=uuid4(),
        tool="om_recall",
        state="grounded",
        stage="core_result",
        delivery="unknown",
        adoption="unknown",
        host_session_id=None,
        injection_elapsed_ms=None,
        elapsed_ms=1.0,
        raw_count=sum(item.kind == "raw" for item in references),
        memory_count=sum(item.kind == "memory" for item in references),
        references=references,
    )


def memory_ref(key="m", revision=7, source=None):
    return ref("memory", key, revision, source)


def assert_hidden(result, reference, reason="unavailable"):
    assert result.model_dump() == {
        **reference.model_dump(),
        "available": False,
        "title": None,
        "reason": reason,
    }


def test_current_titles_exact_contract_order_and_no_body_in_event(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "m", content="Current memory")
    references = (memory_ref(), ref(), ref())
    value = event(*references)
    before = value.model_dump_json()
    result = resolve_call_references(database, value)
    assert [item.model_dump() for item in result] == [
        {
            **reference.model_dump(),
            "available": True,
            "title": "Current memory" if reference.kind == "memory" else "Synthetic evidence",
            "reason": None,
        }
        for reference in references
    ]
    assert all(isinstance(item, SourceRecordRef) for item in result)
    assert SourceRecordRef.model_validate_json(result[0].model_dump_json()) == result[0]
    assert value.model_dump_json() == before
    assert "Current memory" not in before and "Synthetic evidence" not in before


def test_grants_rechecked_without_cache_and_match_source_access(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "m", links=("e1", "e2"))
        grant(db, "cursor", user="other")
    value = event(ref(), memory_ref())
    assert all(item.available for item in resolve_call_references(database, value))
    # Missing local grants preserve the existing local-read semantics.
    stored = MemoryGovernanceStore(database).get("m", user_id="local")
    assert stored is not None
    with closing(sqlite3.connect(database)) as db, db:
        grant(db, "workbuddy")
    result = resolve_call_references(database, value)
    assert result[0].available
    assert_hidden(result[1], memory_ref())
    assert SourceReadAccess(database).memories([stored]) == []
    with closing(sqlite3.connect(database)) as db, db:
        grant(db, "workbuddy", revoked=None)
        grant(db, "cursor")
    assert all(not item.available for item in resolve_call_references(database, value))
    with closing(sqlite3.connect(database)) as db, db:
        grant(db, "cursor", revoked=None)
    assert all(item.available for item in resolve_call_references(database, value))


@pytest.mark.parametrize("kind", ["raw", "memory"])
def test_revision_change_never_returns_new_or_historical_title(database, kind):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "m")
    reference = ref() if kind == "raw" else memory_ref()
    assert resolve_call_references(database, event(reference))[0].available
    table, column = ("evidence", "verbatim") if kind == "raw" else ("governed_memories", "content")
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(f"UPDATE {table} SET revision=revision+1, {column}='New private body'")
    result = resolve_call_references(database, event(reference))[0]
    assert_hidden(result, reference, "changed")
    current = reference.model_copy(update={"revision": reference.revision + 1})
    assert resolve_call_references(database, event(current))[0].title == "New private body"


@pytest.mark.parametrize(
    "reference",
    [ref(key="missing"), ref(key="other-e"), ref(source="workbuddy"), ref(source=None)],
)
def test_raw_missing_wrong_owner_or_source_is_unavailable(database, reference):
    assert_hidden(resolve_call_references(database, event(reference))[0], reference)


def test_raw_deletion_is_unavailable(database):
    assert resolve_call_references(database, event(ref()))[0].available
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("DELETE FROM evidence WHERE evidence_id='e1'")
    assert_hidden(resolve_call_references(database, event(ref()))[0], ref())


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "deleted"},
        {"deleted": "2026-09-20T00:00:00+00:00"},
        {"user": "other", "links": ("other-e",)},
        {"links": ("missing",)},
        {"links": ("e1", "other-e")},
        {"links": ()},
    ],
)
def test_inactive_or_inaccessible_memory_is_unavailable(database, kwargs):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "m", **kwargs)
    assert_hidden(resolve_call_references(database, event(memory_ref()))[0], memory_ref())


def test_memory_source_must_match_active_citation_and_inactive_links_do_not_revoke(database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "m", links=("e1", "e2"))
        db.execute("UPDATE governed_memory_evidence SET active=0 WHERE evidence_id='e2'")
        grant(db, "workbuddy")
    assert resolve_call_references(database, event(memory_ref()))[0].available
    assert resolve_call_references(database, event(memory_ref(source="cursor")))[0].available
    reference = memory_ref(source="workbuddy")
    assert_hidden(resolve_call_references(database, event(reference))[0], reference)
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE governed_memory_evidence SET active=0")
    assert_hidden(resolve_call_references(database, event(memory_ref()))[0], memory_ref())


@pytest.mark.parametrize("kind", ["raw", "memory"])
def test_title_is_bounded_by_unicode_codepoints_in_sql(database, monkeypatch, kind):
    content = "\U0001f9e0\u8bb0\u5fc6e\u0301" * 1000
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "m", content=content)
        db.execute("UPDATE evidence SET verbatim=?", (content,))
    connect = sqlite3.connect
    returned = []

    class BoundedCursor(sqlite3.Cursor):
        def fetchone(self):
            row = super().fetchone()
            if row is not None:
                assert all(not isinstance(item, str) or len(item) <= 120 for item in row)
                returned.append(row)
            return row

    class BoundedConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            return self.cursor(BoundedCursor).execute(sql, parameters)

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: connect(*a, **kw, factory=BoundedConnection)
    )
    reference = ref() if kind == "raw" else memory_ref()
    result = resolve_call_references(database, event(reference))[0]
    assert result.title == content[:120] and len(result.title) == 120
    assert returned


def test_denied_and_changed_references_never_read_text_or_run_freshness(database, monkeypatch):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "revoked", links=("e1", "e2"))
        put(db, "changed")
        grant(db, "workbuddy")
        db.execute("UPDATE evidence SET revision=2 WHERE evidence_id='e1'")
    connect = sqlite3.connect
    denied_reads = []

    def guarded_connect(*args, **kwargs):
        db = connect(*args, **kwargs)

        def authorizer(action, table, column, *_):
            if action == sqlite3.SQLITE_READ and column in ("content", "verbatim", "body"):
                denied_reads.append((table, column))
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        db.set_authorizer(authorizer)
        return db

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    references = (
        ref(),
        memory_ref("changed", revision=6),
        memory_ref("revoked"),
        ref(key="e2", source="workbuddy"),
        ref(key="other-e"),
    )
    result = resolve_call_references(database, event(*references))
    for item, reference, reason in zip(
        result,
        references,
        ("changed", "changed", "unavailable", "unavailable", "unavailable"),
        strict=True,
    ):
        assert_hidden(item, reference, reason)
    assert denied_reads == []


def test_readonly_no_store_initialization_no_schema_or_data_writes_and_connection_closed(
    database, monkeypatch
):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "m")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    connect = sqlite3.connect
    connections = []
    statements = []

    def guarded_connect(database_uri, **kwargs):
        assert database_uri == database.resolve().as_uri() + "?mode=ro"
        db = connect(database_uri, **kwargs)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            db.execute("UPDATE governed_memories SET content='bad' WHERE memory_id='m'")
        db.rollback()
        db.set_trace_callback(statements.append)
        connections.append(db)
        return db

    def forbidden_init(*args, **kwargs):
        pytest.fail("Resolver must not construct a schema-writing store")

    monkeypatch.setattr(sqlite3, "connect", guarded_connect)
    monkeypatch.setattr(MemoryGovernanceStore, "__init__", forbidden_init)
    result = resolve_call_references(database, event(ref(), memory_ref()))
    assert all(item.available for item in result)
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    assert len(connections) == 1
    assert statements and all(
        sql.startswith(("SELECT", "BEGIN", "PRAGMA query_only=ON", "PRAGMA query_only = ON"))
        for sql in statements
    )
    # Pooled: handed back rather than held, and closed with the pool.
    assert main_database(database).stats().readers_in_use == 0
    close_main_databases()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


@pytest.mark.parametrize("broken", ["missing", "invalid", "blank", "directory"])
def test_unreadable_database_is_whole_fixed_error_not_deleted(tmp_path, broken):
    path = tmp_path / broken
    if broken == "invalid":
        path.write_bytes(b"not sqlite")
    elif broken == "blank":
        path.touch()
    elif broken == "directory":
        path.mkdir()
    for value in (event(ref()), event()):
        with pytest.raises(CallReferencesUnavailable, match="^call_references_unavailable$"):
            resolve_call_references(path, value)
    if broken == "missing":
        assert not path.exists()


def test_empty_event_on_valid_database_is_empty(database):
    assert resolve_call_references(database, event()) == []


@pytest.mark.parametrize("fault", ["sql", "os", "malformed-title"])
def test_late_failure_discards_entire_result_and_redacts_exception(database, monkeypatch, fault):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "m")
        if fault == "malformed-title":
            db.execute("UPDATE governed_memories SET content=x'ff'")
    connect = sqlite3.connect

    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            if "substr(content" in sql:
                if fault == "sql":
                    raise sqlite3.OperationalError("private path and body")
                if fault == "os":
                    raise OSError("private path and body")
            return super().execute(sql, parameters)

    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: connect(*a, **kw, factory=FailingConnection)
    )
    with pytest.raises(CallReferencesUnavailable) as error:
        resolve_call_references(database, event(ref(), memory_ref()))
    assert str(error.value) == "call_references_unavailable"
    assert error.value.__suppress_context__


def test_response_rejects_oversized_title_and_invalid_states():
    base = {**ref().model_dump(), "available": True, "title": "title", "reason": None}
    for update in (
        {"title": "x" * 121},
        {"available": False},
        {"reason": "changed"},
        {"title": None},
        {"body": "not allowed"},
    ):
        with pytest.raises(ValidationError):
            SourceRecordRef(**{**base, **update})


def test_actual_evidence_proof_revision_and_corrected_memory(fresh):
    path, evidence, value, record, memory, tasks, output = fresh
    reference = memory_ref(memory.memory_id, memory.revision)
    raw = ref(key=record.evidence_id)
    assert all(item.available for item in resolve_call_references(path, event(raw, reference)))
    evidence.put(value.model_copy(update={"verbatim": "revised evidence"}))
    result = resolve_call_references(path, event(raw, reference))
    # Raw refs still track evidence.revision; memory refs no longer use freshness.
    assert_hidden(result[0], raw, "changed")
    assert result[1].available
    corrected = as_person(MemoryGovernanceStore(path)).correct(
        memory.memory_id,
        user_id="local",
        request_id="correction",
        expected_revision=memory.revision,
        content="explicitly corrected claim",
    )
    assert_hidden(resolve_call_references(path, event(reference))[0], reference, "changed")
    current = memory_ref(corrected.memory_id, corrected.revision)
    assert resolve_call_references(path, event(current))[0].title == corrected.content


def test_scope_only_revision_keeps_memory_readable_when_evidence_changes(fresh):
    path, evidence, value, record, memory, tasks, output = fresh
    updated = as_person(MemoryGovernanceStore(path)).set_scopes(
        memory.memory_id,
        user_id="local",
        request_id="scope",
        expected_revision=memory.revision,
        scopes=("workspace:changed",),
    )
    reference = memory_ref(updated.memory_id, updated.revision)
    assert resolve_call_references(path, event(reference))[0].available
    evidence.put(value.model_copy(update={"verbatim": "revised evidence"}))
    assert resolve_call_references(path, event(reference))[0].available


def test_evidence_identity_change_without_revision_keeps_memory_readable(fresh):
    path, evidence, value, record, memory, tasks, output = fresh
    reference = memory_ref(memory.memory_id, memory.revision)
    assert resolve_call_references(path, event(reference))[0].available
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE evidence SET workspace='workspace:changed'")
    assert resolve_call_references(path, event(reference))[0].available


def test_invalid_proof_is_ignored_but_revocation_still_hides_memory(fresh):
    path, evidence, value, record, memory, tasks, output = fresh
    reference = memory_ref(memory.memory_id, memory.revision)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("UPDATE memory_task_outputs SET body='invalid secret proof'")
    assert all(
        item.available
        for item in resolve_call_references(path, event(ref(key=record.evidence_id), reference))
    )
    with closing(sqlite3.connect(path)) as db, db:
        grant(db, "cursor")
    result = resolve_call_references(path, event(reference))
    assert_hidden(result[0], reference)


def test_missing_active_evidence_precedes_freshness_text_read(fresh):
    path, evidence, value, record, memory, tasks, output = fresh
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("DELETE FROM evidence")
    reference = memory_ref(memory.memory_id, memory.revision)
    assert_hidden(resolve_call_references(path, event(reference))[0], reference)


@pytest.mark.parametrize("change", ["grant", "evidence", "memory", "link"])
def test_authorization_revision_freshness_and_titles_share_one_snapshot(fresh, monkeypatch, change):
    path, evidence, value, record, memory, tasks, output = fresh
    connect = sqlite3.connect
    with closing(connect(path)) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
    changed = []
    reads = []

    class ConcurrentConnection(sqlite3.Connection):
        def execute(self, sql, parameters=()):
            cursor = super().execute(sql, parameters)
            if sql.startswith("SELECT"):
                reads.append(self.in_transaction)
            # Change after memory authorization, before freshness and title.
            if "SELECT EXISTS (SELECT 1 FROM governed_memory_evidence" in sql and not changed:
                with closing(connect(path)) as writer, writer:
                    if change == "grant":
                        grant(writer, "cursor")
                    elif change == "evidence":
                        writer.execute("UPDATE evidence SET revision=revision+1, verbatim='new'")
                    elif change == "memory":
                        writer.execute(
                            "UPDATE governed_memories SET revision=revision+1, content='new'"
                        )
                    else:
                        writer.execute("UPDATE governed_memory_evidence SET active=0")
                changed.append(True)
            return cursor

    # A pooled reader opened while building the fixture would be reused unpatched.
    close_main_databases()
    monkeypatch.setattr(
        sqlite3, "connect", lambda *a, **kw: connect(*a, **kw, factory=ConcurrentConnection)
    )
    reference = memory_ref(memory.memory_id, memory.revision)
    raw = ref(key=record.evidence_id)
    snapshot = resolve_call_references(path, event(reference, raw))
    assert [item.title for item in snapshot] == [memory.content, record.verbatim]
    assert changed == [True] and reads and all(reads)
    current = resolve_call_references(path, event(reference, raw))
    # Memory refs no longer hide on evidence revision/identity via freshness.
    if change == "evidence":
        assert current[0].available
    else:
        assert_hidden(
            current[0], reference, "changed" if change == "memory" else "unavailable"
        )
    assert current[1].available == (change in ("memory", "link"))


def test_deadline_expiry_is_fixed_whole_error(database, monkeypatch):
    from one_memory import call_event_references

    clock = iter((0, 6))
    monkeypatch.setattr(call_event_references.time, "monotonic", lambda: next(clock, 6))
    with pytest.raises(CallReferencesUnavailable, match="^call_references_unavailable$"):
        resolve_call_references(database, event(ref()))


@pytest.mark.parametrize("deleted", ["missing", "removed"])
def test_memory_missing_or_removed_is_unavailable(database, deleted):
    reference = memory_ref()
    if deleted == "removed":
        with closing(sqlite3.connect(database)) as db, db:
            put(db, "m")
        assert resolve_call_references(database, event(reference))[0].available
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("DELETE FROM governed_memory_evidence")
            db.execute("DELETE FROM governed_memories")
    assert_hidden(resolve_call_references(database, event(reference))[0], reference)


@pytest.mark.parametrize("kind", ["raw", "memory"])
@pytest.mark.parametrize("revision", [0, "bad revision", 1.5])
def test_invalid_current_revision_is_whole_error_not_changed(database, kind, revision):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "m")
        table = "evidence" if kind == "raw" else "governed_memories"
        db.execute(f"UPDATE {table} SET revision=?", (revision,))
    reference = ref() if kind == "raw" else memory_ref()
    with pytest.raises(CallReferencesUnavailable, match="^call_references_unavailable$"):
        resolve_call_references(database, event(reference))
