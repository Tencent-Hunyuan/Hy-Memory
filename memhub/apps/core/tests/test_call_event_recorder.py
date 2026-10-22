from datetime import UTC, datetime

from one_memory.api import ConversationSearchHit, ConversationSearchResponse
from one_memory.call_event_recorder import (
    ask_call_event,
    context_call_event,
    conversation_call_event,
)
from one_memory.context_models import ContextPath, ContextPaths, ContextSearchResponse
from one_memory.external_memory_access import ExternalMemoryAccess
from one_memory.memory_governance import GovernedMemory, MemoryKind, MemoryOrigin, MemoryStatus
from one_memory.memory_qa import MemoryAnswer


def _paths(memory: str, raw: str) -> ContextPaths:
    lane = dict(elapsed_ms=1.0, returned_count=0, truncated=False)
    return ContextPaths(
        raw=ContextPath(status=raw, **lane),
        memory=ContextPath(status=memory, **lane),
    )


def _context(memory: str, raw: str) -> ContextSearchResponse:
    return ContextSearchResponse(
        request_id="11111111-1111-4111-8111-111111111111",
        state="partial",
        paths=_paths(memory, raw),
        items=(),
        used_characters=0,
        truncated=False,
        total_ms=3.5,
    )


def test_denied_memory_lane_is_attributed_to_conversations():
    event = context_call_event(_context("not_authorized", "ok"), None)
    assert event.tool == "om_conversations"


def test_requested_memory_lane_stays_recall():
    event = context_call_event(_context("empty", "not_authorized"), None)
    assert event.tool == "om_recall"


def test_zero_result_timeout_is_recorded_separately_from_unavailable() -> None:
    lane = dict(elapsed_ms=750.0, returned_count=0)
    result = ContextSearchResponse(
        request_id="11111111-1111-4111-8111-111111111111",
        state="partial",
        paths=ContextPaths(
            raw=ContextPath(
                status="degraded",
                reason="timeout_budget",
                truncated=True,
                truncated_by=("timeout_budget",),
                **lane,
            ),
            memory=ContextPath(status="empty", truncated=False, **lane),
        ),
        items=(),
        used_characters=0,
        truncated=True,
        total_ms=1500,
    )

    assert context_call_event(result, None).state == "timed_out"


def test_conversation_search_records_counts_without_query_or_excerpt():
    result = ConversationSearchResponse(
        hits=(
            ConversationSearchHit(
                evidence_id="e1",
                conversation_id="c1",
                source_id="cursor",
                excerpt="SECRET_EXCERPT",
            ),
            ConversationSearchHit(
                evidence_id="e2",
                conversation_id="c1",
                source_id="cursor",
                excerpt="SECRET_EXCERPT_2",
            ),
        )
    )
    access = ExternalMemoryAccess(
        installation_id="00000000-0000-4000-8000-000000000001",
        revision=1,
        scopes=frozenset({"all-apps", "source:cursor"}),
        tools=frozenset({"om_conversations"}),
        host_id="codex",
    )
    event = conversation_call_event(result, access, 12.0)
    encoded = event.model_dump_json()
    assert event.tool == "om_conversations"
    assert event.state == "grounded"
    assert event.raw_count == 1 and event.memory_count == 0 and not event.references
    assert event.host_id == "codex"
    assert "SECRET" not in encoded


def test_ask_records_memory_refs_without_answer():
    memory = GovernedMemory(
        memory_id="m1",
        user_id="local",
        kind=MemoryKind.CHAT,
        status=MemoryStatus.CONFIRMED,
        content="SECRET_MEMORY",
        scopes=("all-apps",),
        evidence_ids=("e1",),
        origin=MemoryOrigin.EXTRACTED,
        revision=4,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        deleted_at=None,
    )
    event = ask_call_event(
        MemoryAnswer(answer="SECRET_ANSWER", memories=(memory,), mode="extractive"),
        None,
        8.0,
    )
    encoded = event.model_dump_json()
    assert event.tool == "om_ask" and event.state == "grounded"
    assert event.memory_count == 1 and event.references[0].id == "m1"
    assert event.references[0].revision == 4
    assert "SECRET" not in encoded
