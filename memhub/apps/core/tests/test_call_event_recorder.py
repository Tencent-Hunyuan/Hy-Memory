from datetime import UTC, datetime

from one_memory.call_event_recorder import ask_call_event
from one_memory.memory_governance import GovernedMemory, MemoryKind, MemoryOrigin, MemoryStatus
from one_memory.memory_qa import MemoryAnswer


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
