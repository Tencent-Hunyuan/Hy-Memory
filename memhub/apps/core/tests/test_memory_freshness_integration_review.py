"""Independent synthetic read-path review; no lifespan, model or user database."""

import json
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

import pytest

from one_memory import source_read_access
from one_memory.context_memory import read_memory_context
from one_memory.context_models import ContextSearchRequest
from one_memory.imports.grants import GRANT_SCHEMA
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_evidence_freshness import register_memory_freshness
from one_memory.memory_governance import MemoryDraft, MemoryGovernanceStore, MemoryKind
from one_memory.memory_tasks import MemoryTaskOutput, MemoryTaskStore, evidence_ref
from one_memory.source_read_access import SourceReadAccess
from tests.person_door import as_person


@pytest.fixture
def case(tmp_path):
    path = tmp_path / "fixture.sqlite3"
    evidence = EvidenceStore(path)
    governance = MemoryGovernanceStore(path)
    tasks = MemoryTaskStore(path)
    with closing(sqlite3.connect(path)) as db:
        db.executescript(GRANT_SCHEMA)
    value = EvidenceInput(
        source_id="cursor",
        conversation_id="fixture",
        message_id="one",
        role="user",
        written_by=WrittenBy.PERSON,
        verbatim="alpha beta old claim",
        origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
    )
    record = evidence.put(value).record
    candidate = as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content="alpha beta old claim",
            scopes=("all-apps",),
            evidence_ids=(record.evidence_id,),
        ),
        request_id="create",
    )
    memory = candidate
    output = MemoryTaskOutput(
        task_id="fixture-task",
        memory_id=memory.memory_id,
        memory_revision=memory.revision,
        hy_memory_id="fixture-hy",
        owner="user",
        layer="L2_FACT",
        engine_version="fixture",
        support_refs=(evidence_ref(record),),
    )
    tasks.save_output(output)
    return SimpleNamespace(
        path=path,
        evidence=evidence,
        governance=governance,
        tasks=tasks,
        value=value,
        record=record,
        memory=memory,
        output=output,
    )


def context(case, *, deadline=None):
    return read_memory_context(
        case.path,
        ContextSearchRequest(query="alpha beta"),
        scopes=None,
        deadline=deadline or time.monotonic() + 2,
    )


def visible(case, memory=None):
    return (
        len(context(case).items),
        len(SourceReadAccess(case.path).memories([memory or case.memory])),
    )


def revise(case):
    return case.evidence.put(
        case.value.model_copy(update={"verbatim": "alpha beta new claim"})
    ).record


def test_revised_evidence_remains_readable_after_freshness_retirement(case):
    """Evidence revision no longer hides library / context / source reads."""
    assert visible(case) == (1, 1)
    revise(case)
    assert visible(case) == (1, 1)
    assert case.governance.get(case.memory.memory_id, user_id="local") == case.memory
    assert case.tasks.outputs(case.output.task_id) == (case.output,)


@pytest.mark.parametrize(
    "revise_first", [False, True], ids=["scope-before-revision", "scope-after-revision"]
)
def test_scope_only_revision_keeps_memory_readable_after_evidence_change(case, revise_first):
    if revise_first:
        revise(case)
        assert visible(case) == (1, 1)
    changed = as_person(case.governance).set_scopes(
        case.memory.memory_id,
        user_id="local",
        request_id="scope-only",
        expected_revision=case.memory.revision,
        scopes=("workspace:fixture",),
    )
    assert changed.content == case.memory.content
    if not revise_first:
        assert visible(case, changed) == (1, 1)
        revise(case)
    assert visible(case, changed) == (1, 1)


def test_explicit_correct_remains_readable_but_never_overrides_revocation(case):
    revise(case)
    corrected = as_person(case.governance).correct(
        case.memory.memory_id,
        user_id="local",
        request_id="explicit-correct",
        expected_revision=case.memory.revision,
        content="alpha beta explicit correction",
    )
    assert visible(case, corrected) == (1, 1)
    with closing(sqlite3.connect(case.path)) as db, db:
        db.execute(
            """INSERT INTO source_grants
            (user_id,source_id,root_path,requested_path,root_identity,
             candidate_kind,granted_at,revoked_at,revision)
            VALUES ('local','cursor','/fixture','/fixture','fixture','default',?,?,1)""",
            ("2026-09-20T00:00:00Z", "2026-09-20T00:00:01Z"),
        )
    assert visible(case, corrected) == (0, 0)


def test_missing_proof_is_legacy_compatibility_not_a_new_authorization(case):
    with closing(sqlite3.connect(case.path)) as db, db:
        db.execute("DELETE FROM memory_task_outputs")
    revise(case)
    assert visible(case) == (1, 1)
    result = read_memory_context(
        case.path,
        ContextSearchRequest(query="alpha beta"),
        scopes=frozenset({"workspace:other"}),
        deadline=time.monotonic() + 2,
    )
    assert result.items == ()


def test_revocation_before_freshness_snapshot_cannot_release_memory(case, monkeypatch):
    assert SourceReadAccess(case.path).memories([case.memory]) == [case.memory]
    register = source_read_access.register_memory_freshness
    changes = []

    def revoke_then_register(db):
        # Revocation commits before any SELECT pins the freshness transaction.
        with closing(sqlite3.connect(case.path)) as writer, writer:
            writer.execute(
                """INSERT INTO source_grants
                (user_id,source_id,root_path,requested_path,root_identity,
                 candidate_kind,granted_at,revoked_at,revision)
                VALUES ('local','cursor','/fixture','/fixture','fixture','default',?,?,1)""",
                ("2026-09-20T00:00:00Z", "2026-09-20T00:00:01Z"),
            )
        changes.append(True)
        return register(db)

    monkeypatch.setattr(source_read_access, "register_memory_freshness", revoke_then_register)
    returned = SourceReadAccess(case.path).memories([case.memory])
    assert changes == [True]
    assert returned == []


def test_udf_stays_current_across_evidence_revision(case):
    with closing(sqlite3.connect(case.path)) as reader:
        reader.execute("PRAGMA journal_mode=WAL")
        reader.execute("BEGIN")
        predicate = register_memory_freshness(reader)
        sql = f"SELECT {predicate} FROM governed_memories m"
        assert reader.execute(sql).fetchone() == (1,)
        revise(case)
        assert reader.execute(sql).fetchone() == (1,)
        reader.commit()
        reader.execute("BEGIN")
        register_memory_freshness(reader)
        assert reader.execute(sql).fetchone() == (1,)


def _oversize(case, bad):
    with closing(sqlite3.connect(case.path)) as db, db:
        if bad == "oversize-evidence":
            db.execute("UPDATE evidence SET verbatim=zeroblob(?)", (8 * 1024 * 1024 + 1,))
        else:
            db.execute("UPDATE memory_task_outputs SET body=?", (" " * 65537,))


@pytest.mark.parametrize("bad", ["oversize-proof", "oversize-evidence"])
def test_material_too_large_to_verify_reads_as_current(case, bad):
    """D76: a budget overrun is not stale and must not fail the whole read."""
    _oversize(case, bad)
    assert len(SourceReadAccess(case.path).memories([case.memory])) == 1
    assert context(case).status != "unavailable"


@pytest.mark.parametrize("bad", ["duplicate-key"])
def test_bad_proof_material_no_longer_makes_reads_unavailable(case, bad):
    with closing(sqlite3.connect(case.path)) as db, db:
        db.execute(
            "UPDATE memory_task_outputs SET body=?",
            ('{"memory_revision":2,"memory_revision":2}',),
        )
    assert visible(case) == (1, 1)
    result = context(case)
    assert result.status != "unavailable"
    assert len(result.items) == 1


def test_expired_context_budget_never_reads_proofs(case):
    result = context(case, deadline=time.monotonic() - 1)
    assert result.status == "unavailable" and result.reason == "timeout_budget"


def test_indexed_unrelated_proofs_keep_small_read_bounded(case):
    with closing(sqlite3.connect(case.path)) as db, db:
        db.executemany(
            "INSERT INTO memory_task_outputs VALUES (?,?,?)",
            (
                (f"task-{index}", f"unrelated-{index}", json.dumps({"unrelated": True}))
                for index in range(4000)
            ),
        )
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT body FROM memory_task_outputs WHERE memory_id=? LIMIT 65",
            (case.memory.memory_id,),
        ).fetchall()
        assert any("memory_task_outputs_memory" in row[3] for row in plan)
    started = time.monotonic()
    assert visible(case) == (1, 1)
    elapsed = time.monotonic() - started
    print(f"indexed 4000 unrelated proofs, four reads: {elapsed:.6f}s")
    assert elapsed < 2
