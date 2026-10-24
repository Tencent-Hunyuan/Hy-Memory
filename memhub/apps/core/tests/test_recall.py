import asyncio
import hashlib

import pytest

from one_memory.hy_sdk_types import HySdkMemoryRecord
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_governance import MemoryDraft, MemoryGovernanceStore, MemoryKind
from one_memory.recall import (
    GovernedRecallTarget,
    RecallRequest,
    RecallState,
    SdkRecallTarget,
)
from one_memory.semantic_deadline import SemanticMemoryHit
from tests.person_door import as_person


def _confirmed_memory(governance, evidence, *, content: str, scope: str):
    request_key = hashlib.sha256(f"{scope}:{content}".encode()).hexdigest()
    memory = as_person(governance).create(
        MemoryDraft(
            user_id="local",
            kind=MemoryKind.CHAT,
            content=content,
            scopes=(scope,),
            evidence_ids=(evidence.evidence_id,),
        ),
        request_id=f"create:{request_key}",
    )
    return memory


def test_sdk_recall_does_not_invent_per_memory_harness_acl() -> None:
    async def search(_query):
        return (
            SemanticMemoryHit(
                memory_id="personal",
                score=0.9,
                formatted_content="formatted personal plus private member",
                member_ids=("member",),
                sdk_formatted=True,
                formatting_complete=True,
            ),
            SemanticMemoryHit(
                memory_id="private",
                score=0.8,
                formatted_content="private",
                sdk_formatted=True,
                formatting_complete=True,
            ),
        )

    async def get_memory(memory_id):
        return HySdkMemoryRecord(
            memory_id=memory_id,
            content="personal root" if memory_id == "personal" else "private",
            layer="l2_fact",
            status="active",
            is_latest=True,
            user_id="local",
            agent_id="one-memory",
            gmt_created=1_700_000_000,
        )

    receipt = asyncio.run(
        SdkRecallTarget(
            semantic_search=search,
            get_memory=get_memory,
        ).recall_async(RecallRequest(query="preference"))
    )

    assert receipt.state is RecallState.GROUNDED
    assert [hit.memory_id for hit in receipt.hits] == ["personal", "private"]
    assert receipt.hits[0].content == "formatted personal plus private member"
    assert receipt.hits[0].scopes == ()


def test_recall_is_confirmed_only_grounded_and_scope_bound(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    evidence = evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="我是产品经理，项目采用本地优先架构。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    personal = _confirmed_memory(
        governance,
        evidence,
        content="用户是一名产品经理",
        scope="all-apps",
    )
    _confirmed_memory(
        governance,
        evidence,
        content="项目采用本地优先架构",
        scope="workspace:v1:project",
    )
    target = GovernedRecallTarget(governance)

    result = target.recall(RecallRequest(query="产品经理"))

    assert result.state is RecallState.GROUNDED
    assert [hit.memory_id for hit in result.hits] == [personal.memory_id]
    assert result.hits[0].evidence_ids == (evidence.evidence_id,)
    assert all("workspace:v1:project" not in hit.scopes for hit in result.hits)

    workspace = target.recall(
        RecallRequest(
            query="项目采用什么架构？",
            scope="workspace:v1:project",
        )
    )
    assert [hit.content for hit in workspace.hits] == ["项目采用本地优先架构"]


def test_wildcard_scope_means_every_scope_just_like_it_does_in_qa(tmp_path) -> None:
    """`*` must mean the same thing on both read paths.

    Hosts recall through this path while the in-app answerer uses its own, and
    there `*` is a wildcard. Treating `*` here as a literal scope name matches
    nothing, so a host asking across scopes gets a silent empty result rather
    than the memory it was promised.
    """
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    evidence = evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="登录鉴权方案讨论：方式A、方式B、方式C 的优劣评估。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    project = _confirmed_memory(
        governance,
        evidence,
        content="登录鉴权方案讨论：方式A、方式B、方式C 的优劣评估",
        scope="workspace:v1:project",
    )
    target = GovernedRecallTarget(governance)

    # The default personal scope must still not see project memories.
    assert target.recall(RecallRequest(query="登录鉴权方案")).state is RecallState.EMPTY

    across = target.recall(RecallRequest(query="登录鉴权方案", scope="*"))

    assert across.state is RecallState.GROUNDED
    assert [hit.memory_id for hit in across.hits] == [project.memory_id]


def test_recall_returns_empty_instead_of_unrelated_fallback(tmp_path) -> None:
    target = GovernedRecallTarget(MemoryGovernanceStore(tmp_path / "one-memory.sqlite3"))

    result = target.recall(RecallRequest(query="下周航班"))

    assert result.state is RecallState.EMPTY
    assert result.hits == ()


def test_recall_finds_a_single_chinese_character_query(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    evidence = evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="张 的偏好",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    memory = _confirmed_memory(
        governance,
        evidence,
        content="张 的偏好",
        scope="all-apps",
    )

    result = GovernedRecallTarget(governance).recall(RecallRequest(query="张"))
    stopped = GovernedRecallTarget(governance).recall(RecallRequest(query="我"))

    assert result.state is RecallState.GROUNDED
    assert [hit.memory_id for hit in result.hits] == [memory.memory_id]
    assert stopped.state is RecallState.EMPTY
    assert stopped.hits == ()


def test_recall_stop_term_does_not_query_governance(tmp_path, monkeypatch) -> None:
    governance = MemoryGovernanceStore(tmp_path / "one-memory.sqlite3")

    def forbidden(*args, **kwargs):
        pytest.fail("Stop-term-only recall must not query governed memory storage")

    monkeypatch.setattr(governance, "find_confirmed_matching_terms", forbidden)

    result = GovernedRecallTarget(governance).recall(RecallRequest(query="我"))

    assert result.state is RecallState.EMPTY
    assert result.hits == ()


def test_recall_rejects_single_generic_term_overlap(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    evidence = evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="将计划交给 WorkBuddy 进行方案评估优化。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    _confirmed_memory(
        governance,
        evidence,
        content="将计划交给 WorkBuddy 进行方案评估优化",
        scope="workspace:v1:project",
    )

    result = GovernedRecallTarget(governance).recall(
        RecallRequest(
            query="这个项目的法律和开源许可方案是什么？",
            scope="workspace:v1:project",
        )
    )

    assert result.state is RecallState.EMPTY


def test_recall_uses_semantic_ids_when_words_do_not_overlap(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    evidence = evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="我是产品经理。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    personal = _confirmed_memory(
        governance,
        evidence,
        content="用户是一名产品经理",
        scope="all-apps",
    )

    async def semantic_search(_: str) -> tuple[str, ...]:
        return (personal.memory_id,)

    target = GovernedRecallTarget(governance, semantic_search=semantic_search)
    result = asyncio.run(target.recall_async(RecallRequest(query="我的职业是什么？")))

    assert [hit.memory_id for hit in result.hits] == [personal.memory_id]
    assert result.hits[0].score == 1


def test_recall_skips_oversized_hit_and_keeps_smaller_matches(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    evidence = evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="产品经理偏好清晰沟通",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    small = _confirmed_memory(
        governance,
        evidence,
        content="产品经理偏好清晰沟通",
        scope="all-apps",
    )
    _confirmed_memory(
        governance,
        evidence,
        content="产品经理" + ("很" * 250),
        scope="all-apps",
    )

    result = GovernedRecallTarget(governance).recall(
        RecallRequest(query="产品经理", character_budget=200)
    )

    assert [hit.memory_id for hit in result.hits] == [small.memory_id]
    assert result.truncated is True


def test_recall_async_keeps_strong_semantic_when_weak_lexical_already_hits(tmp_path) -> None:
    """A weak substring hit must not skip the semantic channel.

    Chinese bigrams make everyday phrasing look grounded: 「开源许可证怎么选」
    overlaps 「开源」「许可」 on a recent review note, while the actual MIT
    decision has no literal overlap. Returning at the first non-empty lexical
    page would make that decision unreachable.
    """
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    evidence = evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="开源许可讨论。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    strong = _confirmed_memory(
        governance,
        evidence,
        content="用户决定 One Memory 采用 MIT 协议发布。",
        scope="all-apps",
    )
    weak = _confirmed_memory(
        governance,
        evidence,
        content="这个开源项目的许可方案还在评审中，尚未定稿。",
        scope="all-apps",
    )
    searched: list[str] = []

    async def semantic_search(query: str) -> tuple[str, ...]:
        searched.append(query)
        return (strong.memory_id,)

    result = asyncio.run(
        GovernedRecallTarget(governance, semantic_search=semantic_search).recall_async(
            RecallRequest(query="开源许可证怎么选")
        )
    )

    assert searched == ["开源许可证怎么选"]
    ids = [hit.memory_id for hit in result.hits]
    assert strong.memory_id in ids
    assert weak.memory_id in ids
    assert ids[0] == strong.memory_id


def test_recall_async_keeps_lexical_hits_when_semantic_times_out(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    governance = MemoryGovernanceStore(database)
    evidence = evidence_store.put(
        EvidenceInput(
            source_id="cursor",
            conversation_id="conversation",
            message_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim="开源许可讨论。",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record
    strong = _confirmed_memory(
        governance,
        evidence,
        content="用户决定 One Memory 采用 MIT 协议发布。",
        scope="all-apps",
    )
    weak = _confirmed_memory(
        governance,
        evidence,
        content="这个开源项目的许可方案还在评审中，尚未定稿。",
        scope="all-apps",
    )

    started = False

    async def semantic_search(_query: str) -> tuple[str, ...]:
        nonlocal started
        started = True
        await asyncio.sleep(4)
        return (strong.memory_id,)

    result = asyncio.run(
        GovernedRecallTarget(governance, semantic_search=semantic_search).recall_async(
            RecallRequest(query="开源许可证怎么选")
        )
    )

    assert started is True
    ids = [hit.memory_id for hit in result.hits]
    assert ids == [weak.memory_id]
    assert strong.memory_id not in ids
