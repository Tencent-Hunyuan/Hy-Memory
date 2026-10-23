"""The ask path must reach a memory the newest-N page no longer holds.

`GovernedRecallTarget` was fixed for this and says so in its own source:
semantic hits are fetched by id, and the content filter runs in SQL, because
scoring one newest-500 page made a match buried under newer memories look
like it never existed.

The answerer still read the library that way, so `/api/v1/memories/ask`
answered "现有记忆不足以回答这个问题" for a memory the library held and the
search path still returned. The two paths disagreed on the same library.

Filler rows are bulk-inserted so the test measures reach, not confirm
latency, and each test states which lane has to find the memory.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import (
    MemoryDraft,
    MemoryGovernanceStore,
    MemoryKind,
    MemoryStatus,
)
from one_memory.memory_qa import MemoryAnswerer
from one_memory.model_profiles import ModelProfileStore
from one_memory.secrets import InMemorySecretStore
from tests.person_door import as_person

pytestmark = pytest.mark.contract

FILLER_COUNT = 600
TARGET_CONTENT = "用户从事 AI4S 方向的自动化研究，长期维护 AGENTS.md 工作纪律。"
#: The answer is in the memory's wording and not in the query, so only the
#: semantic lane can reach it.
SEMANTIC_QUERY = "我从事什么职业"
#: This one names the memory's own words, so the lexical lane must reach it.
LEXICAL_QUERY = "AI4S 自动化研究"


def _library(tmp_path, *, filler: int, scope: str = "all-apps"):
    """One readable memory buried under `filler` newer ones."""
    database = tmp_path / "one-memory.sqlite3"
    evidence = (
        EvidenceStore(database)
        .put(
            EvidenceInput(
                source_id="cursor",
                conversation_id="c",
                message_id="m",
                role="user",
                written_by=WrittenBy.PERSON,
                verbatim=TARGET_CONTENT,
                origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
            )
        )
        .record
    )
    governance = MemoryGovernanceStore(database)
    target = as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content=TARGET_CONTENT,
            scopes=(scope,),
            evidence_ids=(evidence.evidence_id,),
        ),
        request_id="create:target",
    )
    _bulk_filler(governance, count=filler, newer_than=target.updated_at, scope=scope)
    answerer = MemoryAnswerer(
        governance=governance,
        profiles=ModelProfileStore(database),
        secrets=InMemorySecretStore(),
    )
    return answerer, target


def _bulk_filler(
    governance: MemoryGovernanceStore,
    *,
    count: int,
    newer_than: datetime,
    scope: str,
) -> None:
    """Confirmed filler rows, each newer than the memory they bury."""
    start = newer_than + timedelta(seconds=1)
    rows = [
        (
            f"filler-{index:05d}",
            "local",
            "chat",
            "confirmed",
            f"无关记忆条目 filler{index}",
            f'["{scope}"]',
            1,
            (start + timedelta(seconds=index)).isoformat(),
            (start + timedelta(seconds=index)).isoformat(),
        )
        for index in range(count)
    ]
    with governance._connection() as connection:
        connection.executemany(
            """
            INSERT INTO governed_memories (
                memory_id, user_id, kind, status, content, scopes_json,
                revision, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def _newest_page(governance: MemoryGovernanceStore) -> list[str]:
    page = governance.list(
        user_id="local",
        status=MemoryStatus.CONFIRMED,
        limit=500,
    )
    return [memory.memory_id for memory in page]


def _reached(answerer, question: str, *, semantic_id: str | None = None) -> list[str]:
    """Memory ids the ask path would ground an answer in."""

    async def semantic_search(_query: str):
        return (semantic_id,) if semantic_id else ()

    answerer.semantic_search = semantic_search
    memories = asyncio.run(answerer.retrieve(question, scope="all-apps"))
    return [memory.memory_id for memory in memories]


def test_ask_reaches_a_lexical_match_that_is_not_among_the_newest_500(tmp_path) -> None:
    """The lexical lane must find a match the newest-N page no longer holds.

    The candidate page is now the library's newest 500 with no term filter,
    so a match buried under 600 newer memories is scored against nothing and
    the answer says the memory is not there.
    """
    answerer, target = _library(tmp_path, filler=FILLER_COUNT)

    assert target.memory_id not in _newest_page(answerer.governance), (
        "fixture is not buried; the test would pass without the fix"
    )
    reached = _reached(answerer, LEXICAL_QUERY)

    assert target.memory_id in reached


def test_ask_reaches_a_semantic_hit_that_is_not_among_the_newest_500(tmp_path) -> None:
    """HY's first-ranked id must survive being older than one page.

    Semantic ids were intersected with the candidate page, so a first-ranked
    HY id was dropped for the sole reason that it was not recent — the same
    failure recall's own source calls out and fixed.
    """
    answerer, target = _library(tmp_path, filler=FILLER_COUNT)

    assert target.memory_id not in _newest_page(answerer.governance)
    reached = _reached(answerer, SEMANTIC_QUERY, semantic_id=target.memory_id)

    assert target.memory_id in reached


def test_ask_still_works_just_below_the_boundary(tmp_path) -> None:
    """Control: proves the two tests above fail on size, not on wording.

    Without it a broken query or a bad fixture would look identical to the
    defect, and the fix could be declared done by making the answerer match
    nothing.
    """
    answerer, target = _library(tmp_path, filler=499)

    assert target.memory_id in _newest_page(answerer.governance)
    reached = _reached(answerer, SEMANTIC_QUERY, semantic_id=target.memory_id)

    assert target.memory_id in reached


def test_ask_does_not_widen_what_the_caller_may_read(tmp_path) -> None:
    """Guard: a wider search must not widen the caller's scopes.

    Fetching a semantic hit by id reaches past the candidate page, so the
    scope check has to travel with it — otherwise a first-ranked id would
    hand back a memory this host may not read.
    """
    answerer, target = _library(tmp_path, filler=FILLER_COUNT, scope="other-app")

    assert target.memory_id not in _newest_page(answerer.governance)
    reached = _reached(answerer, SEMANTIC_QUERY, semantic_id=target.memory_id)

    assert target.memory_id not in reached
