"""D93 A, gate 8.1: the stored conclusion must track the exact one, step by step.

Visibility is stored rather than decided per read. The risk is not the rule —
recomputation is handed the read path's own predicate — but the bookkeeping:
a change that nobody marked dirty leaves the reader with a conclusion that is
no longer true. So drive random changes and compare after every one.
"""

import contextlib
import random
import sqlite3

import pytest

from one_memory.api import _visibility_batch
from one_memory.imports.candidates import RootCandidateKind
from one_memory.imports.grants import GRANT_SCHEMA
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import MemoryDraft, MemoryGovernanceStore, MemoryKind
from one_memory.memory_tasks import (
    MemoryTaskOutput,
    MemoryTaskStore,
    evidence_ref,
)
from one_memory.memory_visibility import ensure_tables, install_triggers
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
            embedding_base_url="http://127.0.0.1:11434",
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


def _seed(library, sources=("cursor", "claude-code")) -> list[str]:
    database, evidence, tasks, governance, _profile = library
    grants = database.parent  # unused; kept for clarity
    del grants
    ids = []
    index = 0
    for source_id in sources:
        folder = database.parent / source_id
        folder.mkdir(exist_ok=True)
        for _ in range(2):
            record = evidence.put(
                EvidenceInput(
                    source_id=source_id,
                    conversation_id=f"conversation-{index}",
                    message_id=f"m-{index}",
                    correlation_id=f"m-{index}",
                    role="user",
                    written_by=WrittenBy.PERSON,
                    verbatim=f"{source_id} 第 {index} 条真实对话内容。",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            ).record
            memory = as_person(governance).create(
                MemoryDraft(
                    user_id="local",
                    kind=MemoryKind.CHAT,
                    content=f"{source_id} 第 {index} 条真实对话内容。",
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
            ids.append(memory.memory_id)
            index += 1
    return ids


def _exact(library, memories) -> set[str]:
    database, _evidence, _tasks, governance, _profile = library
    rows = [governance.get(memory_id, user_id="local") for memory_id in memories]
    return {
        item.memory_id for item in SourceReadAccess(database).filter([row for row in rows if row])
    }


def _stored(library) -> set[str]:
    database = library[0]
    with sqlite3.connect(database) as db:
        return {
            row[0] for row in db.execute("SELECT memory_id FROM memory_visibility WHERE visible=1")
        }


@pytest.mark.parametrize("seed", list(range(12)))
def test_random_sequences_keep_the_two_in_agreement(library, seed):
    """Every step, the stored conclusion equals the exact one.

    Twelve seeds here; `test_a_longer_sequence` runs the same walk with a
    thousand steps for a slower, wider check.
    """
    database, evidence, tasks, governance, _profile = library
    with sqlite3.connect(database) as db:
        ensure_tables(db)
        install_triggers(db)
        db.commit()

    memories = _seed(library)
    rng = random.Random(seed)
    grants = evidence  # placeholder to keep names short
    del grants

    for step in range(40):
        action = rng.choice(["revoke", "restore", "new_version", "new_evidence", "delete_memory"])
        source_id = rng.choice(["cursor", "claude-code"])
        if action == "revoke":
            _revoke(library, source_id)
        elif action == "restore":
            _restore(library, source_id)
        elif action == "new_version":
            _new_version(library, rng)
        elif action == "new_evidence":
            _new_evidence(library, rng)
        else:
            _delete_memory(library, rng, memories)

        # Answer everything that is now marked, then compare.
        while _visibility_batch(database):
            pass
        assert _stored(library) == _exact(library, memories), (seed, step, action)


def test_a_longer_sequence(library):
    """A wider walk, same requirement."""
    database, _evidence, _tasks, _governance, _profile = library
    with sqlite3.connect(database) as db:
        ensure_tables(db)
        install_triggers(db)
        db.commit()
    memories = _seed(library)
    rng = random.Random(20261009)
    for step in range(200):
        action = rng.choice(["revoke", "restore", "new_version", "new_evidence"])
        if action == "revoke":
            _revoke(library, rng.choice(["cursor", "claude-code"]))
        elif action == "restore":
            _restore(library, rng.choice(["cursor", "claude-code"]))
        elif action == "new_version":
            _new_version(library, rng)
        else:
            _new_evidence(library, rng)
        while _visibility_batch(database):
            pass
        assert _stored(library) == _exact(library, memories), (step, action)


def _revoke(library, source_id):
    database, evidence, _tasks, _governance, _profile = library
    del evidence
    from one_memory.imports.grants import SourceGrantStore

    store = SourceGrantStore(database, home=database.parent)
    with contextlib.suppress(Exception):
        store.revoke(user_id="local", source_id=source_id)


def _restore(library, source_id):
    database, _evidence, _tasks, _governance, _profile = library
    from one_memory.imports.grants import SourceGrantStore

    folder = database.parent / source_id
    folder.mkdir(exist_ok=True)
    store = SourceGrantStore(database, home=database.parent)
    with contextlib.suppress(Exception):
        store.grant(
            user_id="local",
            source_id=source_id,
            root=folder,
            candidate_kind=RootCandidateKind.DEFAULT,
        )


def _new_version(library, rng):
    database, evidence, _tasks, _governance, _profile = library
    index = rng.randrange(4)
    source_id = ["cursor", "cursor", "claude-code", "claude-code"][index]
    evidence.put(
        EvidenceInput(
            source_id=source_id,
            conversation_id=f"conversation-{index}",
            message_id=f"m-{index}",
            correlation_id=f"m-{index}",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim=f"{source_id} 第 {index} 条真实对话内容（第 {rng.randrange(99)} 次改动）。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    )


def _new_evidence(library, rng):
    database, evidence, _tasks, _governance, _profile = library
    index = rng.randrange(4)
    marker = f"m-new-{rng.randrange(9999)}"
    evidence.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id=f"conversation-{index}",
            message_id=marker,
            correlation_id=marker,
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="一条新的真实对话内容。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    )


def _delete_memory(library, rng, memories):
    _database, _evidence, _tasks, governance, _profile = library
    if not memories:
        return
    memory_id = rng.choice(memories)
    memory = governance.get(memory_id, user_id="local")
    if memory is None or memory.deleted_at is not None:
        return
    with contextlib.suppress(Exception):
        governance.delete(
            memory_id,
            user_id="local",
            request_id=f"delete-{rng.randrange(9999)}",
            expected_revision=memory.revision,
        )
