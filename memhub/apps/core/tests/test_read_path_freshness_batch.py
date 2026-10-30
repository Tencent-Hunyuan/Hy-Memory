"""The row-by-row read check must not scale its query count with the page."""

import sqlite3

import pytest

from one_memory.imports.grants import GRANT_SCHEMA
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import MemoryDraft, MemoryGovernanceStore, MemoryKind
from one_memory.memory_tasks import (
    MemoryTaskOutput,
    MemoryTaskStore,
    evidence_ref,
)
from one_memory.model_profiles import (
    ModelCapabilities,
    ModelProfileInput,
    ModelProfileStore,
    ProviderKind,
)
from one_memory.secrets import InMemorySecretStore
from one_memory.source_read_access import SourceReadAccess
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
    memories = []
    for index in range(12):
        record = evidence.put(
            EvidenceInput(
                source_id="cursor",
                conversation_id=f"conversation-{index}",
                message_id=f"m-{index}",
                correlation_id=f"m-{index}",
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim=f"第 {index} 条真实对话内容。",
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        ).record
        memory = as_person(governance).create(
            MemoryDraft(
                user_id="local",
                kind=MemoryKind.CHAT,
                content=f"第 {index} 条真实对话内容。",
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
        memories.append(memory)
    return database, memories


def test_the_read_does_not_scale_its_query_count_with_the_page(library):
    """D90: the freshness UDF is the read path's whole cost.

    Measured on the old-user library over 160 memories: the UDF is 0.69 s and
    the citation subquery 0.08 s. The UDF runs several queries per memory —
    proofs, versions, active citations, evidence sizes — so the count grows
    with the page, and the cost grows again when the library is being written
    to. That is what put both the legacy memory list and the map over their five
    second interrupt.

    The per-memory lookups can be read once for the whole page. Quadrupling
    the page must not quadruple the statements.
    """
    database, memories = library

    import one_memory.source_read_access as read_access

    calls: list[int] = []
    real = read_access.freshness_proofs_prefetched

    def counting(db, memory_ids, *, evidence_ids=()):
        calls.append(len(list(memory_ids)))
        return real(db, memory_ids, evidence_ids=evidence_ids)

    def queries_for(subset):
        calls.clear()
        SourceReadAccess(database).filter(subset)
        return list(calls)

    read_access.freshness_proofs_prefetched = counting
    try:
        small = queries_for(memories[:3])
        large = queries_for(memories)
    finally:
        read_access.freshness_proofs_prefetched = real

    # One batched read per call, carrying the whole page — not one per memory.
    assert small == [3]
    assert large == [12]
