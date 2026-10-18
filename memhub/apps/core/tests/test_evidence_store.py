import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from pydantic import ValidationError

from one_memory import ingest_journal
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.storage import EvidenceStore
from one_memory.memory_tasks import evidence_ref, matches_evidence_ref
from one_memory.workspace_resolution import KnownWorkspace


def evidence(
    *,
    source_id: str = "cursor",
    message_id: str | None = "message-1",
    correlation_id: str | None = None,
    verbatim: str = "Remember the source, not the guess.",
    origin: EvidenceOrigin = EvidenceOrigin.FILE,
    written_by: WrittenBy = WrittenBy.PERSON,
    role: str = "user",
    source_ordinal: str | None = None,
) -> EvidenceInput:
    return EvidenceInput(
        source_id=source_id,
        conversation_id="conversation-1",
        message_id=message_id,
        correlation_id=correlation_id,
        source_ordinal=source_ordinal
        if source_ordinal is not None
        else ("line:1" if message_id is None and correlation_id is None else None),
        role=role,
        written_by=written_by,
        created_at=datetime(2026, 8, 31, 12, 0, tzinfo=UTC),
        verbatim=verbatim,
        origin_meta={
            origin: OriginMetadata(
                raw_meta={"schema": "fixture-v1"},
                source_version="fixture-1",
            )
        },
    )


def forget_repair(connection: sqlite3.Connection, name: str) -> None:
    """Make a database look like one written before a repair existed.

    The repairs are backfills of rows an older build wrote, so the state that
    triggers them is exactly this: rows needing fixing, and no record of the
    fix. Hand-breaking a row without also clearing the record would be testing
    a state no real database is ever in.
    """
    connection.execute("DELETE FROM evidence_repairs WHERE name = ?", (name,))


def test_same_source_message_merges_origins_without_duplication(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    first = store.put(evidence())
    second = store.put(evidence(origin=EvidenceOrigin.HOOK))

    assert first.was_created is True
    assert second.was_created is False
    assert second.was_revised is False
    assert second.record.origins == {
        EvidenceOrigin.FILE,
        EvidenceOrigin.HOOK,
    }
    assert set(second.record.origin_meta) == second.record.origins
    assert store.count() == 1


def test_identical_text_from_different_sources_is_not_deduplicated(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(evidence(source_id="cursor"))
    store.put(evidence(source_id="claude-code"))

    assert store.count() == 2


def test_rescanning_an_unchanged_file_does_not_write_the_message_again(tmp_path) -> None:
    """A rescan is idempotent because the identity is derived from the content.

    `EvidenceInput.idempotency_key` is a hash of the source, conversation,
    protocol role and content hash, and `evidence_id` is a uuid5 over that
    key, so replaying an unchanged file reproduces the same primary key and
    `ON CONFLICT` absorbs it. Verified: removing the anchoring logic still
    leaves this green, so this pins the contract, not one implementation.
    """
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    first = store.put(evidence(message_id=None, source_ordinal="line:1"))
    second = store.put(evidence(message_id=None, source_ordinal="line:1"))

    assert first.was_created is True
    assert second.was_created is False
    assert second.record.evidence_id == first.record.evidence_id
    assert store.count() == 1


def test_derived_identity_uses_role_not_host_author_guess(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    first = store.put(evidence(message_id=None, written_by=WrittenBy.UNKNOWN))
    replay = store.put(evidence(message_id=None, written_by=WrittenBy.PERSON))
    assistant = store.put(evidence(message_id=None, role="assistant", written_by=WrittenBy.AGENT))

    assert replay.record.evidence_id == first.record.evidence_id
    assert replay.was_created is False
    assert assistant.record.evidence_id != first.record.evidence_id
    assert store.count() == 2


def test_task_proof_ignores_legacy_written_by_metadata(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    record = store.put(evidence()).record
    proof = evidence_ref(record)

    changed_author = record.model_copy(update={"written_by": WrittenBy.AGENT})
    legacy_identity = record.model_dump(
        mode="json",
        exclude={"origin_meta", "imported_at", "updated_at"},
    )
    legacy_proof = {
        **proof,
        "identity_hash": sha256(
            json.dumps(legacy_identity, sort_keys=True).encode()
        ).hexdigest(),
    }

    assert matches_evidence_ref(changed_author, proof) is True
    assert matches_evidence_ref(changed_author, legacy_proof) is True


def test_rescan_reuses_a_row_stored_under_written_by_derived_v3(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(database)
    item = evidence(message_id=None, written_by=WrittenBy.UNKNOWN)
    original = store.put(item).record
    with sqlite3.connect(database) as connection, connection:
        connection.execute(
            "UPDATE evidence SET idempotency_key=? WHERE evidence_id=?",
            (item.written_by_derived_v3_idempotency_key, original.evidence_id),
        )

    replay = store.put(evidence(message_id=None, written_by=WrittenBy.PERSON))

    assert replay.was_created is False
    assert replay.record.evidence_id == original.evidence_id
    assert store.count() == 1


def test_legacy_identity_role_change_revises_the_existing_evidence(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(database)
    old = evidence(
        message_id=None,
        role="assistant",
        written_by=WrittenBy.AGENT,
    )
    original = store.put(old).record
    with sqlite3.connect(database) as connection, connection:
        connection.execute(
            "UPDATE evidence SET idempotency_key=? WHERE evidence_id=?",
            (old.written_by_derived_v3_idempotency_key, original.evidence_id),
        )

    replay = store.put(
        evidence(
            message_id=None,
            role="user",
            written_by=WrittenBy.AGENT,
        )
    )

    assert replay.record.evidence_id == original.evidence_id
    assert replay.was_revised is True
    assert replay.record.revision == original.revision + 1
    assert replay.record.role == "user"
    assert store.count() == 1


def test_changed_text_at_one_position_is_a_new_message_not_a_silent_edit(tmp_path) -> None:
    """An edited line becomes new evidence, never an overwrite of the old one.

    The content is part of the identity, so a rewrite lands in a new row and
    the earlier wording stays available to reconciliation. Overwriting in place
    would quietly destroy evidence the person may still be relying on.
    """
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(evidence(message_id=None, source_ordinal="line:1", verbatim="first"))
    store.put(evidence(message_id=None, source_ordinal="line:1", verbatim="second"))

    assert store.count() == 2


def test_source_totals_are_grouped_in_one_snapshot(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(evidence(source_id="cursor", message_id="cursor-1"))
    store.put(evidence(source_id="cursor", message_id="cursor-2"))
    store.put(evidence(source_id="claude-code", message_id="claude-1"))

    assert store.source_totals_by_source(user_id="local") == {
        "claude-code": (1, 1),
        "cursor": (2, 1),
    }


def test_personal_candidate_prefilter_excludes_unrelated_user_turns(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    stable = store.put(
        evidence(
            message_id="stable",
            verbatim="我偏好简洁、克制的中文界面文案。",
        )
    ).record
    store.put(
        evidence(
            message_id="task",
            verbatim="请检查这个页面并运行测试。",
        )
    )
    store.put(
        evidence(
            message_id="assistant",
            verbatim="我偏好简洁文案。",
            role="assistant",
            written_by=WrittenBy.AGENT,
        )
    )

    candidates = store.list_personal_candidates()

    assert [record.evidence_id for record in candidates] == [stable.evidence_id]


def test_workspace_candidate_prefilter_requires_explicit_cue_and_scope(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    stable = store.put(
        evidence(
            message_id="workspace-memory",
            verbatim="录音纪要要挂在事件流，其他没问题，可以记录结论。",
        ).model_copy(update={"workspace": "workspace:v1:project"})
    ).record
    store.put(
        evidence(
            message_id="no-cue",
            verbatim="请优化录音纪要页面。",
        ).model_copy(update={"workspace": "workspace:v1:project"})
    )
    store.put(
        evidence(
            message_id="no-workspace",
            verbatim="这个可以记一下。",
        ).model_copy(update={"conversation_id": "personal-conversation"})
    )

    candidates = store.list_workspace_candidates()

    assert [record.evidence_id for record in candidates] == [stable.evidence_id]


def test_store_migrates_stale_cursor_host_turns_away_from_person(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(database)
    stale = store.put(
        evidence(
            message_id="generated",
            verbatim=(
                "Briefly inform the user about the task result "
                "and perform any follow-up actions (if needed)."
            ),
        )
    ).record
    telemetry = store.put(
        evidence(
            message_id="pipeline-log",
            verbatim=(
                '{"request_id":"pipeline-request","type":"write","steps":[{"detail":"truncated'
            ),
        )
    ).record
    reader_log = store.put(
        evidence(
            message_id="reader-log",
            verbatim=json.dumps(
                {
                    "success": True,
                    "request_id": "reader-request",
                    "reader": "client",
                    "recommended_order": ["READ_REQUEST"],
                }
            ),
        )
    ).record
    assert stale.written_by is WrittenBy.PERSON
    assert telemetry.written_by is WrittenBy.PERSON
    assert reader_log.written_by is WrittenBy.PERSON
    with sqlite3.connect(database) as connection:
        forget_repair(connection, "cursor_host_authorship")

    migrated = EvidenceStore(database).get(stale.evidence_id)
    migrated_telemetry = EvidenceStore(database).get(telemetry.evidence_id)
    migrated_reader = EvidenceStore(database).get(reader_log.evidence_id)

    assert migrated is not None
    assert migrated.written_by is WrittenBy.AGENT
    assert migrated.revision == 2
    assert migrated_telemetry is not None
    assert migrated_telemetry.written_by is WrittenBy.AGENT
    assert migrated_telemetry.revision == 2
    assert migrated_reader is not None
    assert migrated_reader.written_by is WrittenBy.AGENT
    assert migrated_reader.revision == 2


def test_hook_correlation_id_merges_with_later_source_message_id(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    hook = store.put(
        evidence(
            message_id=None,
            correlation_id="message-1",
            origin=EvidenceOrigin.HOOK,
        )
    )
    file_scan = store.put(evidence(message_id="message-1"))

    assert hook.record.evidence_id == file_scan.record.evidence_id
    assert file_scan.record.message_id == "message-1"
    assert file_scan.record.origins == {
        EvidenceOrigin.FILE,
        EvidenceOrigin.HOOK,
    }
    assert store.count() == 1


def test_same_message_id_merges_hook_without_workspace_into_file_workspace(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    hook = store.put(
        evidence(message_id="message-1", origin=EvidenceOrigin.HOOK).model_copy(
            update={"workspace": None}
        )
    )
    file_scan = store.put(
        evidence(message_id="message-1").model_copy(update={"workspace": "workspace:v1:project"})
    )

    assert file_scan.record.evidence_id == hook.record.evidence_id
    assert file_scan.record.workspace == "workspace:v1:project"
    assert file_scan.record.origins == {EvidenceOrigin.FILE, EvidenceOrigin.HOOK}
    assert store.count() == 1


def test_file_adoption_refreshes_the_ingest_identity_in_the_same_transaction(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(database)
    with store._connect(writing=True) as connection:
        ingest_journal.initialize(connection)
    hook = store.put(
        evidence(
            source_id="workbuddy",
            message_id=None,
            correlation_id=None,
            origin=EvidenceOrigin.HOOK,
            verbatim="我喜欢蓝莓",
        )
    ).record
    with sqlite3.connect(database) as connection:
        connection.execute("BEGIN")
        before = ingest_journal.current(connection, "local", hook.evidence_id)
        assert before is not None
        connection.rollback()

    adopted = store.put(
        evidence(
            source_id="workbuddy",
            message_id="file-message",
            origin=EvidenceOrigin.FILE,
            verbatim="我喜欢蓝莓",
        )
    ).record

    with sqlite3.connect(database) as connection:
        connection.execute("BEGIN")
        current = ingest_journal.current(connection, "local", hook.evidence_id)
        assert current is not None
        assert current.seq > before.seq
        assert current.generation_eligible is True
        assert ingest_journal.matches_current(connection, current)
        connection.rollback()
    assert adopted.evidence_id == hook.evidence_id


def test_hook_adopts_one_recent_file_row_but_not_ambiguous_repeated_text(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    first = store.put(
        evidence(
            source_id="workbuddy",
            message_id="file-first",
            origin=EvidenceOrigin.FILE,
            verbatim="我喜欢蓝莓",
        )
    ).record
    hook_input = evidence(
        source_id="workbuddy",
        message_id=None,
        correlation_id=None,
        origin=EvidenceOrigin.HOOK,
        verbatim="我喜欢蓝莓",
    ).model_copy(update={"source_ordinal": "hook-visible"})

    merged = store.put(hook_input)

    assert merged.record.evidence_id == first.evidence_id
    assert merged.record.origins == {EvidenceOrigin.FILE, EvidenceOrigin.HOOK}
    assert store.count() == 1

    store.put(
        evidence(
            source_id="workbuddy",
            message_id="file-second",
            origin=EvidenceOrigin.FILE,
            verbatim="真的重复发送",
        )
    )
    store.put(
        evidence(
            source_id="workbuddy",
            message_id="file-third",
            origin=EvidenceOrigin.FILE,
            verbatim="真的重复发送",
        )
    )
    ambiguous = store.put(hook_input.model_copy(update={"verbatim": "真的重复发送"}))

    assert ambiguous.was_created is True
    assert store.count() == 4


def test_less_informed_correlated_origin_cannot_erase_known_attribution(
    tmp_path,
) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    file_evidence = evidence().model_copy(update={"workspace": "/repo", "project": "one-memory"})
    original = store.put(file_evidence)
    hook_evidence = file_evidence.model_copy(
        update={
            "message_id": None,
            "correlation_id": "message-1",
            "workspace": None,
            "project": None,
            "created_at": None,
            "written_by": WrittenBy.UNKNOWN,
            "origin_meta": {EvidenceOrigin.HOOK: OriginMetadata()},
        }
    )

    merged = store.put(hook_evidence)

    assert merged.was_revised is False
    assert merged.record.revision == 1
    assert merged.record.workspace == "/repo"
    assert merged.record.project == "one-memory"
    assert merged.record.created_at == original.record.created_at
    assert merged.record.written_by is WrittenBy.PERSON


def test_cursor_conversation_propagates_unique_workspace_across_channels(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    unscoped = store.put(evidence(message_id="vscdb-message")).record
    scoped = evidence(message_id="transcript-message").model_copy(
        update={
            "workspace": "workspace:v1:project",
            "project": "one-memory",
        }
    )

    store.put(scoped)
    inherited = store.put(evidence(message_id="later-vscdb-message")).record

    propagated = store.get(unscoped.evidence_id)
    assert propagated is not None
    assert propagated.workspace == "workspace:v1:project"
    assert propagated.project == "one-memory"
    assert inherited.workspace == "workspace:v1:project"
    assert inherited.project == "one-memory"


def test_unique_workspace_repair_keeps_content_and_identity_stable(tmp_path) -> None:
    """Workspace discovery may repair metadata, but it is not a source revision."""
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    before = store.put(evidence(message_id="unscoped-before-workspace")).record
    store.put(
        evidence(message_id="scoped-after-workspace").model_copy(
            update={"workspace": "workspace:v1:single-project", "project": "single-project"}
        )
    )

    after = store.get(before.evidence_id)

    assert after is not None
    assert after.evidence_id == before.evidence_id
    assert after.revision == before.revision
    assert after.content_hash == before.content_hash
    assert after.verbatim == before.verbatim
    assert after.workspace == "workspace:v1:single-project"
    assert after.project == "single-project"


def test_workbuddy_conversation_propagates_sibling_metadata_workspace(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    unscoped = store.put(
        evidence(source_id="workbuddy", message_id="message-before-metadata")
    ).record
    store.put(
        evidence(source_id="workbuddy", message_id="message-with-metadata").model_copy(
            update={
                "workspace": "workspace:v1:workbuddy-project",
                "project": "experience-square",
            }
        )
    )

    propagated = store.get(unscoped.evidence_id)

    assert propagated is not None
    assert propagated.workspace == "workspace:v1:workbuddy-project"
    assert propagated.project == "experience-square"


def test_cursor_conversation_does_not_propagate_conflicting_workspaces(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(evidence(message_id="project-a").model_copy(update={"workspace": "workspace:v1:a"}))
    store.put(evidence(message_id="project-b").model_copy(update={"workspace": "workspace:v1:b"}))

    ambiguous = store.put(evidence(message_id="ambiguous")).record

    assert ambiguous.workspace is None


def test_store_migrates_legacy_cursor_conversation_workspace(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(database)
    legacy = store.put(evidence(message_id="legacy-vscdb")).record
    store.put(
        evidence(message_id="scoped-transcript").model_copy(
            update={"workspace": "workspace:v1:project"}
        )
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE evidence SET workspace = NULL WHERE evidence_id = ?",
            (legacy.evidence_id,),
        )
        forget_repair(connection, "cursor_conversation_attribution")

    migrated = EvidenceStore(database).get(legacy.evidence_id)

    assert migrated is not None
    assert migrated.workspace == "workspace:v1:project"


def test_source_edit_creates_a_new_revision_without_losing_identity(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    first = store.put(evidence(verbatim="Original"))
    revised = store.put(evidence(verbatim="Edited at source"))

    assert revised.was_revised is True
    assert revised.record.evidence_id == first.record.evidence_id
    assert revised.record.revision == 2
    assert revised.record.verbatim == "Edited at source"


def test_origin_metadata_is_preserved_independently_without_phantom_revision(
    tmp_path,
) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    original = evidence()
    store.put(original)
    combined = original.model_copy(
        update={
            "workspace": "/new/workspace",
            "origin_meta": {
                EvidenceOrigin.FILE: OriginMetadata(
                    raw_meta={"path": "/chat.jsonl", "line": 42},
                    source_version="fixture-2",
                ),
                EvidenceOrigin.HOOK: OriginMetadata(
                    raw_meta={"event": "post-message"},
                    source_version="hook-1",
                ),
            },
        }
    )

    result = store.put(combined)

    assert result.was_revised is False
    assert result.record.revision == 1
    assert result.record.workspace == "/new/workspace"
    assert result.record.origin_meta[EvidenceOrigin.FILE].raw_meta["line"] == 42
    assert result.record.origin_meta[EvidenceOrigin.HOOK].raw_meta["event"] == "post-message"


def test_derived_identity_normalizes_whitespace_but_keeps_author_explicit(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    first = store.put(
        evidence(
            message_id=None,
            verbatim="Agent   replay",
            written_by=WrittenBy.AGENT,
        )
    )
    second = store.put(
        evidence(
            message_id=None,
            verbatim="Agent replay",
            written_by=WrittenBy.AGENT,
        )
    )

    assert first.record.evidence_id == second.record.evidence_id
    assert second.record.written_by is WrittenBy.AGENT
    assert store.count() == 1


def test_concurrent_retries_claim_one_evidence_identity(tmp_path) -> None:
    database = tmp_path / "one-memory.sqlite3"
    EvidenceStore(database)

    def retry() -> bool:
        return EvidenceStore(database).put(evidence()).was_created

    with ThreadPoolExecutor(max_workers=4) as executor:
        created = list(executor.map(lambda _: retry(), range(8)))

    assert created.count(True) == 1
    assert EvidenceStore(database).count() == 1


def test_evidence_rejects_empty_text_and_unbounded_metadata() -> None:
    with pytest.raises(ValidationError):
        EvidenceInput(
            source_id="fixture",
            conversation_id="conversation",
            role="user",
            written_by=WrittenBy.PERSON,
            source_ordinal="line:1",
            verbatim=" \n ",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )

    with pytest.raises(ValidationError):
        EvidenceInput(
            source_id="fixture",
            conversation_id="conversation",
            role="user",
            written_by=WrittenBy.PERSON,
            source_ordinal="line:1",
            verbatim="bounded",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata(raw_meta={"oversized": "x" * 70_000})},
        )


def test_known_workspaces_reads_only_an_index(tmp_path) -> None:
    """Recall asks this on every request, so the cost has to stay bounded.

    Measured on this machine's real database before the index existed: the
    same query took 4.2-7.1 seconds over 87,689 rows to return 131, which was
    the entire latency of a recall for every installed host and pushed one real
    run past the client's timeout. A scan is therefore not a slow answer here,
    it is a broken one — and it is invisible in a test with three rows, which
    is why this asserts the plan rather than a duration.
    """
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(
        evidence(message_id="in-a-project").model_copy(
            update={"workspace": "/repo", "project": "one-memory"}
        )
    )
    store.put(evidence(message_id="no-workspace"))

    assert store.known_workspaces(user_id="local") == [
        KnownWorkspace(source_id="cursor", workspace="/repo", project="one-memory")
    ]

    with sqlite3.connect(tmp_path / "one-memory.sqlite3") as connection:
        plan = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT source_id, workspace, project
            FROM evidence
            WHERE user_id = ? AND workspace IS NOT NULL
            GROUP BY source_id, workspace, project
            """,
            ("local",),
        ).fetchall()
    steps = [row[3] for row in plan]
    # Covering, so the table is never touched; and no sort step, because the
    # index is ordered the way the grouping needs.
    assert any("COVERING INDEX evidence_workspace_index" in step for step in steps), steps
    assert not any("TEMP B-TREE" in step for step in steps), steps


def test_a_backfill_is_carried_out_once_and_not_on_every_open(tmp_path) -> None:
    """Opening the store must not rescan the corpus every time.

    This is the whole point of the ledger. Measured on this machine's real
    database, re-running these two repairs on open cost 68 of the 91 seconds a
    launch took, growing with the corpus, and the desktop app gave up waiting
    before Core was ready — a working install became one that would not start.

    The cost cannot be asserted here, where three rows scan instantly, so what
    is asserted is the behaviour that causes it: a second open does no repair
    work at all.
    """
    database = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(database)
    scoped = store.put(
        evidence(message_id="scoped").model_copy(update={"workspace": "workspace:v1:project"})
    ).record

    with sqlite3.connect(database) as connection:
        applied = {row[0] for row in connection.execute("SELECT name FROM evidence_repairs")}
        # Recorded on first open even though there was nothing to fix: the
        # question the record answers is "has this run", not "did it change
        # anything".
        assert applied == {
            "cursor_host_authorship",
            "cursor_conversation_attribution",
            "conversation_search_index",
        }
        # A row that the attribution repair would fix if it ran again.
        connection.execute(
            "UPDATE evidence SET workspace = NULL WHERE evidence_id = ?",
            (scoped.evidence_id,),
        )

    reopened = EvidenceStore(database).get(scoped.evidence_id)

    assert reopened is not None
    assert reopened.workspace is None


def test_opening_the_store_twice_at_once_does_not_fail(tmp_path) -> None:
    """Two processes may open the same database at the same moment.

    The app's Core and a developer's both do, and the repair claim is a write.
    Taken inside the schema transaction it could not wait for the other
    writer — SQLite refuses to upgrade a transaction that has already read,
    without honouring the busy timeout — so opening the store became a coin
    flip. Four at once is enough to have caught that.
    """
    database = tmp_path / "one-memory.sqlite3"

    with ThreadPoolExecutor(max_workers=4) as executor:
        stores = list(executor.map(lambda _: EvidenceStore(database), range(4)))

    assert len(stores) == 4
    with sqlite3.connect(database) as connection:
        # Claimed exactly once each, whichever opener got there first.
        counts = connection.execute(
            "SELECT name, COUNT(*) FROM evidence_repairs GROUP BY name"
        ).fetchall()
    assert sorted(counts) == [
        ("conversation_search_index", 1),
        ("cursor_conversation_attribution", 1),
        ("cursor_host_authorship", 1),
    ]
