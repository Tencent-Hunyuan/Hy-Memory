"""D94: an empty result names the sources this host may not read.

In this build the source boundary is a grant row, not a scope: a host that
never granted a source reads none of that source's memories. The unit-level
case writes through `write_from_hy`, the same path a real import uses, then
leaves the grant out — exactly what WorkBuddy's host did against Codex
content.
"""

import sqlite3
from contextlib import closing

import pytest

from one_memory.database_access import close_main_databases
from one_memory.imports.grants import GRANT_SCHEMA
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import (
    HyCreate,
    MemoryDraft,
    MemoryGovernanceStore,
    MemoryKind,
    MemoryOrigin,
)
from one_memory.memory_tasks import MemoryTaskOutput, MemoryTaskStore, evidence_ref
from one_memory.recall import GovernedRecallTarget, RecallRequest
from one_memory.source_read_access import SourceReadAccess

NOW = "2026-09-19T01:00:00+00:00"


@pytest.fixture
def excluded_source(tmp_path):
    path = tmp_path / "fixture.sqlite3"
    evidence = EvidenceStore(path)
    governance = MemoryGovernanceStore(path)
    tasks = MemoryTaskStore(path)
    with closing(sqlite3.connect(path)) as db:
        db.executescript(GRANT_SCHEMA)
    value = EvidenceInput(
        source_id="codex",
        conversation_id="fixture",
        message_id="one",
        role="user",
        written_by=WrittenBy.PERSON,
        verbatim="codex 第 0 条真实对话内容。",
        origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
    )
    record = evidence.put(value).record
    memory = governance.write_from_hy(
        HyCreate(
            draft=MemoryDraft(
                user_id="local",
                kind=MemoryKind.CHAT,
                content="codex 第 0 条真实对话内容。",
                scopes=("all-apps",),
                evidence_ids=(record.evidence_id,),
                origin=MemoryOrigin.EXTRACTED,
            ),
            request_id="hy-0",
            memory_id="hy-memory-0",
        ),
        user_id="local",
        derivation=governance.hy_derivation((record.evidence_id,)),
    )
    tasks.save_output(
        MemoryTaskOutput(
            task_id="fixture-task",
            memory_id=memory.memory_id,
            memory_revision=memory.revision,
            hy_memory_id="fixture-hy",
            owner="user",
            layer="L2_FACT",
            engine_version="fixture",
            support_refs=(evidence_ref(record),),
        )
    )
    return path


@pytest.fixture(autouse=True)
def closed_databases():
    yield
    close_main_databases()


def test_an_empty_result_names_the_source_it_may_not_read(excluded_source):
    governance = MemoryGovernanceStore(excluded_source)
    candidates = {"hy-memory-0": governance.get("hy-memory-0", user_id="local")}
    target = GovernedRecallTarget(governance)
    target.filter_memories = lambda memories: []
    filtered = tuple(target.filter_memories(tuple(candidates.values())))
    excluded_ids = set(candidates) - {memory.memory_id for memory in filtered}
    sources = governance.sources_behind_memories(user_id="local", memory_ids=tuple(excluded_ids))
    named = {source for mid in excluded_ids for source in sources.get(mid, frozenset())}
    assert not filtered
    assert named == {"codex"}


def test_excluded_sources_collects_the_source_names_directly(excluded_source):
    """Unit-level pin on the D94 collector's contract: evidence source names."""

    class Collector:
        def __init__(self, governance):
            self.governance = governance

        def collect(self, memory_ids):
            sources = self.governance.sources_behind_memories(
                user_id="local",
                memory_ids=memory_ids,
            )
            return sorted(source for sources in sources.values() for source in sources)

    sources = Collector(MemoryGovernanceStore(excluded_source)).collect(("hy-memory-0",))

    assert sources == ["codex"]


def test_a_readable_source_is_not_named_unreadable(excluded_source):
    with closing(sqlite3.connect(excluded_source)) as db, db:
        db.execute(
            "INSERT INTO source_grants(user_id,source_id,root_path,requested_path,"
            "root_identity,candidate_kind,granted_at,revoked_at,revision) "
            "VALUES('local','codex','/synthetic','/synthetic','fixture',"
            "'default',?,NULL,1)",
            (NOW,),
        )

    target = GovernedRecallTarget(MemoryGovernanceStore(excluded_source))
    target.filter_memories = SourceReadAccess(excluded_source).memories
    receipt = target.recall(RecallRequest(query="真实对话内容", scope="all-apps"))

    assert receipt.hits != ()
    assert receipt.excluded_sources == ()
