"""Freshness retirement: read path always admits, even when evidence changed."""

import json
import sqlite3

import pytest

from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_evidence_freshness import (
    budget_errors_raise,
    freshness_proofs_prefetched,
    register_memory_freshness,
)
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


def test_register_returns_constant_true_predicate(case):
    path, evidence, value, record, memory, tasks, output = case
    with sqlite3.connect(path) as db:
        db.execute("BEGIN")
        assert register_memory_freshness(db) == "1"
        result = db.execute(
            "SELECT is_memory_evidence_current(?,?,?)",
            ("x", 1, "local"),
        ).fetchone()
        assert result == (1,)


def test_revised_evidence_no_longer_hides_memory(case):
    path, evidence, value, record, memory, tasks, output = case
    assert check(path, memory) == 1
    evidence.put(value.model_copy(update={"verbatim": "new statement"}))
    assert check(path, memory) == 1


def test_segment_support_refs_do_not_fail_reads(case):
    path, evidence, value, record, memory, tasks, output = case
    body = output.model_dump(mode="json")
    body["support_refs"][0].update(
        segment_index=0,
        segment_start=0,
        segment_end=len(record.verbatim),
        segment_hash="0" * 64,
    )
    with sqlite3.connect(path) as db:
        db.execute("UPDATE memory_task_outputs SET body=?", (json.dumps(body),))
    assert check(path, memory) == 1


def test_malformed_proof_does_not_fail_reads(case):
    path, evidence, value, record, memory, tasks, output = case
    with sqlite3.connect(path) as db:
        db.execute(
            "UPDATE memory_task_outputs SET body=? WHERE memory_id=?",
            ("{", memory.memory_id),
        )
    assert check(path, memory) == 1


def test_missing_table_compatibility_is_readonly():
    with sqlite3.connect(":memory:") as db:
        db.execute("BEGIN")
        assert register_memory_freshness(db) == "1"
        assert db.execute("SELECT is_memory_evidence_current('old',1,'local')").fetchone() == (1,)
        assert db.execute("SELECT name FROM sqlite_master").fetchall() == []


def test_prefetch_is_noop(case):
    path, evidence, value, record, memory, tasks, output = case
    with sqlite3.connect(path) as db:
        db.execute("BEGIN")
        with freshness_proofs_prefetched(db, [memory.memory_id], evidence_ids=[record.evidence_id]):
            assert register_memory_freshness(db) == "1"
            assert check(path, memory) == 1


def test_budget_raise_env_is_inert(case, monkeypatch):
    path, evidence, value, record, memory, tasks, output = case
    monkeypatch.setenv("ONE_MEMORY_FRESHNESS_BUDGET_RAISES", "1")
    assert budget_errors_raise() is False
    for index in range(65):
        tasks.save_output(output.model_copy(update={"task_id": f"task-{index}"}))
    assert check(path, memory) == 1
