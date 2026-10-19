import json
import sqlite3
from hashlib import sha256

import pytest

from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_evidence_freshness import register_memory_freshness
from one_memory.memory_governance import MemoryDraft, MemoryGovernanceStore, MemoryKind
from one_memory.memory_tasks import MemoryTaskOutput, MemoryTaskStore, evidence_ref
from tests.person_door import as_person


@pytest.fixture
def case(tmp_path):
    path = tmp_path / "test.sqlite3"
    evidence = EvidenceStore(path)
    governance = MemoryGovernanceStore(path)
    tasks = MemoryTaskStore(path)
    value = EvidenceInput(
        source_id="cursor",
        conversation_id="test",
        message_id="1",
        role="user",
        written_by=WrittenBy.PERSON,
        verbatim="old statement",
        origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
    )
    record = evidence.put(value).record
    memory = as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content="claim",
            scopes=("all-apps",),
            evidence_ids=(record.evidence_id,),
        ),
        request_id="create",
    )
    output = MemoryTaskOutput(
        task_id="task1",
        memory_id=memory.memory_id,
        memory_revision=memory.revision,
        hy_memory_id="hy1",
        owner="user",
        layer="L2_FACT",
        engine_version="test",
        support_refs=(evidence_ref(record),),
    )
    tasks.save_output(output)
    return path, evidence, value, record, memory, tasks, output


def check(path, memory):
    with sqlite3.connect(path) as db:
        db.execute("BEGIN")
        predicate = register_memory_freshness(db)
        return db.execute(
            f"SELECT {predicate} FROM governed_memories m WHERE m.memory_id=?",
            (memory.memory_id,),
        ).fetchone()[0]


def test_fresh_then_revised_and_identity_only_change(case):
    path, evidence, value, record, memory, tasks, output = case
    assert check(path, memory) == 1
    evidence.put(value.model_copy(update={"verbatim": "new statement"}))
    assert check(path, memory) == 0


def test_segment_support_ref_is_accepted(case):
    """A long Evidence split into segments carries segment_* keys.

    Those keys are part of TaskEvidenceRef; rejecting them made the freshness
    UDF raise, which surfaced as a 503 for the whole memory library.
    """
    path, evidence, value, record, memory, tasks, output = case
    ref = dict(evidence_ref(record))
    ref.update(
        segment_index=0,
        segment_start=0,
        segment_end=len(record.verbatim),
        segment_hash=sha256(record.verbatim.encode()).hexdigest(),
    )
    tasks.save_output(output.model_copy(update={"support_refs": (ref,)}))
    assert check(path, memory) == 1


def test_any_complete_fresh_proof_wins_over_old_proof(case):
    path, evidence, value, record, memory, tasks, output = case
    revised = evidence.put(value.model_copy(update={"verbatim": "new statement"})).record
    tasks.save_output(
        output.model_copy(update={"task_id": "task2", "support_refs": (evidence_ref(revised),)})
    )
    assert check(path, memory) == 1


def test_other_revision_and_incomplete_proof_do_not_hide_legacy_or_correction(case):
    path, evidence, value, record, memory, tasks, output = case
    evidence.put(value.model_copy(update={"verbatim": "new statement"}))
    tasks.save_output(output.model_copy(update={"memory_revision": memory.revision + 1}))
    assert check(path, memory) == 1
    tasks.save_output(output.model_copy(update={"support_refs": ()}))
    assert check(path, memory) == 1


@pytest.mark.parametrize("broken", ["json", "identity", "ref"])
def test_bad_proof_raises_not_false_empty(case, broken):
    path, evidence, value, record, memory, tasks, output = case
    body = output.model_dump(mode="json")
    if broken == "identity":
        body["memory_id"] = "other"
    if broken == "ref":
        body["support_refs"][0]["revision"] = "1"
    payload = json.dumps(body)
    if broken == "json":
        payload = "{"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE memory_task_outputs SET body=?", (payload,))
    with pytest.raises(sqlite3.OperationalError):
        check(path, memory)


def test_missing_table_compatibility_is_readonly():
    with sqlite3.connect(":memory:") as db:
        db.execute("BEGIN")
        assert register_memory_freshness(db) == "1"
        assert db.execute("SELECT is_memory_evidence_current('old',1,'local')").fetchone() == (1,)
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_table_presence_is_checked_once_at_registration(case):
    path, evidence, value, record, memory, tasks, output = case
    with sqlite3.connect(path) as db:
        db.execute("BEGIN")
        statements = []
        db.set_trace_callback(statements.append)
        predicate = register_memory_freshness(db)
        for _ in range(3):
            assert db.execute(f"SELECT {predicate} FROM governed_memories m").fetchone() == (1,)
        assert sum("sqlite_master" in statement for statement in statements) == 1


def test_proof_budget_reads_current_instead_of_failing(case):
    path, evidence, value, record, memory, tasks, output = case
    for index in range(65):
        tasks.save_output(output.model_copy(update={"task_id": f"task-{index}"}))
    assert check(path, memory) == 1


def test_proof_budget_still_raises_when_opted_back(case, monkeypatch):
    path, evidence, value, record, memory, tasks, output = case
    monkeypatch.setenv("ONE_MEMORY_FRESHNESS_BUDGET_RAISES", "1")
    for index in range(65):
        tasks.save_output(output.model_copy(update={"task_id": f"task-{index}"}))
    with pytest.raises(sqlite3.OperationalError):
        check(path, memory)


def test_snapshot_cache_does_not_hide_changes_across_transactions(case):
    path, evidence, value, record, memory, tasks, output = case
    with sqlite3.connect(path) as db:
        db.execute("BEGIN")
        predicate = register_memory_freshness(db)
        query = f"SELECT {predicate} FROM governed_memories m"
        assert db.execute(query).fetchone() == (1,)
        db.commit()
        evidence.put(value.model_copy(update={"verbatim": "new statement"}))
        db.execute("BEGIN")
        register_memory_freshness(db)
        assert db.execute(query).fetchone() == (0,)


def test_identity_hash_detects_metadata_change_without_revision_or_content_change(case):
    path, evidence, value, record, memory, tasks, output = case
    with sqlite3.connect(path) as db:
        db.execute("UPDATE evidence SET workspace='workspace:changed'")
    assert check(path, memory) == 0


def test_incomplete_fresh_proof_cannot_override_complete_stale(case):
    path, evidence, value, record, memory, tasks, output = case
    evidence.put(value.model_copy(update={"verbatim": "new statement"}))
    tasks.save_output(output.model_copy(update={"task_id": "partial", "support_refs": ()}))
    assert check(path, memory) == 0


def test_sql_query_only_and_missing_proof_compatibility(case):
    path, evidence, value, record, memory, tasks, output = case
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM memory_task_outputs")
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        before = db.total_changes
        predicate = register_memory_freshness(db)
        assert db.execute(f"SELECT {predicate} FROM governed_memories m").fetchone() == (1,)
        assert db.total_changes == before


def test_oversized_evidence_reads_current_without_loading_body(case):
    path, evidence, value, record, memory, tasks, output = case
    with sqlite3.connect(path) as db:
        db.execute("UPDATE evidence SET verbatim=zeroblob(?)", (8 * 1024 * 1024 + 1,))
    assert check(path, memory) == 1


@pytest.mark.parametrize("revise_first", [False, True])
def test_scope_only_chain_preserves_old_support_guard(case, revise_first):
    path, evidence, value, record, memory, tasks, output = case
    governance = MemoryGovernanceStore(path)
    if revise_first:
        evidence.put(value.model_copy(update={"verbatim": "new statement"}))
    for index in range(3):
        memory = as_person(governance).set_scopes(
            memory.memory_id,
            user_id="local",
            request_id=f"scope-{index}",
            expected_revision=memory.revision,
            scopes=(f"workspace:{index}",),
        )
    if not revise_first:
        assert check(path, memory) == 1
        evidence.put(value.model_copy(update={"verbatim": "new statement"}))
    assert check(path, memory) == 0


@pytest.mark.parametrize("action", ["correct", "undo", "hy_reconcile", "unknown"])
def test_scope_chain_does_not_cross_non_scope_action(case, action):
    path, evidence, value, record, memory, tasks, output = case
    governance = MemoryGovernanceStore(path)
    memory = as_person(governance).correct(
        memory.memory_id,
        user_id="local",
        request_id="correct",
        expected_revision=memory.revision,
        content=memory.content,
    )
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE governed_memory_versions SET action=? WHERE revision=?",
            (action, memory.revision),
        )
    memory = as_person(governance).set_scopes(
        memory.memory_id,
        user_id="local",
        request_id="scope",
        expected_revision=memory.revision,
        scopes=("workspace:changed",),
    )
    evidence.put(value.model_copy(update={"verbatim": "new statement"}))
    assert check(path, memory) == 1


def test_scope_chain_budget_reads_current(case):
    path, evidence, value, record, memory, tasks, output = case
    governance = MemoryGovernanceStore(path)
    for index in range(65):
        memory = as_person(governance).set_scopes(
            memory.memory_id,
            user_id="local",
            request_id=f"scope-{index}",
            expected_revision=memory.revision,
            scopes=(f"workspace:{index}",),
        )
    assert check(path, memory) == 1


def test_changed_content_in_scope_history_is_not_inherited(case):
    path, evidence, value, record, memory, tasks, output = case
    governance = MemoryGovernanceStore(path)
    memory = as_person(governance).set_scopes(
        memory.memory_id,
        user_id="local",
        request_id="scope",
        expected_revision=memory.revision,
        scopes=("workspace:changed",),
    )
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE governed_memory_versions SET content='different' WHERE revision=?",
            (output.memory_revision,),
        )
    evidence.put(value.model_copy(update={"verbatim": "new statement"}))
    assert check(path, memory) == 1


def test_scope_chain_keeps_large_governed_content_inside_sql(case):
    path, evidence, value, record, memory, tasks, output = case
    with sqlite3.connect(path) as db:
        content = "x" * 1_000_000
        db.execute("UPDATE governed_memories SET content=?", (content,))
        db.execute("UPDATE governed_memory_versions SET content=?", (content,))
    governance = MemoryGovernanceStore(path)
    memory = as_person(governance).set_scopes(
        memory.memory_id,
        user_id="local",
        request_id="scope",
        expected_revision=memory.revision,
        scopes=("workspace:changed",),
    )
    evidence.put(value.model_copy(update={"verbatim": "new statement"}))

    class BoundedCursor(sqlite3.Cursor):
        def fetchone(self):
            row = super().fetchone()
            if row is not None:
                assert all(not isinstance(item, str) or len(item) < 65_536 for item in row)
            return row

    class BoundedConnection(sqlite3.Connection):
        def cursor(self, factory=BoundedCursor):
            return super().cursor(factory)

    with sqlite3.connect(path, factory=BoundedConnection) as db:
        db.execute("BEGIN")
        predicate = register_memory_freshness(db)
        assert db.execute(f"SELECT {predicate} FROM governed_memories m").fetchone() == (0,)


def _confirmed_with_oversized_proof(case):
    path, evidence, value, record, memory, tasks, output = case
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE memory_task_outputs SET body=? WHERE memory_id=?",
            (
                json.dumps({"memory_revision": memory.revision, "pad": "x" * 70_000}),
                memory.memory_id,
            ),
        )
    return path, memory


def test_one_oversized_memory_no_longer_fails_the_library_count(case):
    """D76: one memory whose proof is too large used to 503 the homepage forever."""
    path, memory = _confirmed_with_oversized_proof(case)
    with sqlite3.connect(path) as db:
        db.execute("BEGIN")
        predicate = register_memory_freshness(db)
        count = db.execute(
            f"SELECT COUNT(*) FROM governed_memories m WHERE {predicate}"
        ).fetchone()[0]
    assert count == 1


def test_malformed_proof_still_raises(case):
    path, evidence, value, record, memory, tasks, output = case
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE memory_task_outputs SET body=? WHERE memory_id=?", ("{}", memory.memory_id)
        )
    with pytest.raises(sqlite3.OperationalError):
        check(path, memory)


def test_budget_skip_is_reported_without_content(case, monkeypatch):
    import one_memory.memory_evidence_freshness as freshness

    events = []
    monkeypatch.setattr(freshness, "diagnostic_event", lambda **values: events.append(values))
    monkeypatch.setattr(freshness, "_reported", {})
    path, memory = _confirmed_with_oversized_proof(case)
    for _ in range(3):
        assert check(path, memory) == 1
    assert len(events) == 1
    assert events[0]["details"] == {"reason": "memory_freshness_proof_budget", "count": 1}
    assert "pad" not in json.dumps(events[0])
