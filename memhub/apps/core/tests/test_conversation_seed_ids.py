import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from one_memory.imports.models import (
    EvidenceContentKind,
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.storage import EvidenceStore

AT = datetime(2026, 9, 14, tzinfo=UTC)


def put(
    store: EvidenceStore,
    conversation: str,
    message: str = "seed",
    *,
    source: str = "fixture",
    user: str = "local",
    role: str = "user",
    author: WrittenBy = WrittenBy.PERSON,
    kind: EvidenceContentKind = EvidenceContentKind.RAW_CONVERSATION,
    created_at: datetime | None = AT,
    ordinal: str | None = None,
):
    return store.put(
        EvidenceInput(
            user_id=user,
            source_id=source,
            conversation_id=conversation,
            message_id=message,
            source_ordinal=ordinal,
            role=role,
            written_by=author,
            content_kind=kind,
            created_at=created_at,
            verbatim=f"Synthetic {conversation} {message}",
            origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
        )
    ).record


def test_seed_ids_read_metadata_only_without_hydrating(tmp_path, monkeypatch):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    first = put(store, "one")
    second = put(store, "two")
    reads = []
    select_statements = []
    connect = store._connect

    @contextmanager
    def metadata_connection(**kwargs):
        def authorize(action, table, column, database, trigger):
            if action == sqlite3.SQLITE_READ:
                reads.append((table, column))
                if table != "evidence" or column in {"verbatim", "content_hash"}:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        with connect(**kwargs) as connection:
            connection.set_authorizer(authorize)
            connection.set_trace_callback(
                lambda sql: (
                    select_statements.append(sql)
                    if sql.lstrip().upper().startswith("SELECT")
                    else None
                )
            )
            yield connection

    def forbidden_hydration(*args, **kwargs):
        pytest.fail("ID enumeration hydrated an Evidence record")

    monkeypatch.setattr(store, "_connect", metadata_connection)
    with store._connect() as connection, pytest.raises(sqlite3.DatabaseError):
        connection.execute("SELECT verbatim FROM evidence").fetchall()
    reads.clear()
    select_statements.clear()
    monkeypatch.setattr(store, "get", forbidden_hydration)
    monkeypatch.setattr(store, "_get_with_connection", forbidden_hydration)
    monkeypatch.setattr(store, "list_conversation", forbidden_hydration)

    assert store.list_conversation_seed_ids() == [first.evidence_id, second.evidence_id]
    assert len(select_statements) == 1
    assert ("evidence", "evidence_id") in reads
    assert all(table == "evidence" and column != "verbatim" for table, column in reads)


def test_seed_eligibility_keeps_user_roles_and_memory_content_only(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    put(store, "a-human", "assistant-before", role="assistant", author=WrittenBy.AGENT)
    person = put(store, "a-human", "person")
    report = put(
        store,
        "b-report",
        role="assistant",
        author=WrittenBy.AGENT,
        kind=EvidenceContentKind.MEMORY_CONTENT,
    )
    put(store, "assistant-only", role="assistant", author=WrittenBy.AGENT)
    agent_user = put(store, "agent-authored-user", author=WrittenBy.AGENT)
    unknown_user = put(store, "unknown-user", author=WrittenBy.UNKNOWN)
    put(store, "tool-only", role="tool", author=WrittenBy.TOOL)
    put(store, "other-user", user="other")

    assert store.list_conversation_seed_ids() == [
        person.evidence_id,
        agent_user.evidence_id,
        report.evidence_id,
        unknown_user.evidence_id,
    ]
    assert store.list_conversation_seed_ids(limit=2) == [
        person.evidence_id,
        agent_user.evidence_id,
    ]


def test_latest_activity_includes_nonseed_turns_and_import_time_fallback(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    old_active = put(store, "old-active", created_at=datetime(2020, 1, 1, tzinfo=UTC))
    put(
        store,
        "old-active",
        "recent-assistant",
        role="assistant",
        author=WrittenBy.AGENT,
        created_at=datetime(2026, 9, 13, tzinfo=UTC),
    )
    newer_seed = put(store, "newer-seed", created_at=datetime(2026, 9, 12, tzinfo=UTC))
    unknown = put(store, "unknown", created_at=None)
    with store._connect(writing=True) as connection:
        connection.execute(
            "UPDATE evidence SET imported_at=? WHERE evidence_id=?",
            ("2026-09-14T00:00:00+00:00", unknown.evidence_id),
        )

    assert store.list_conversation_seed_ids() == [
        unknown.evidence_id,
        old_active.evidence_id,
        newer_seed.evidence_id,
    ]
    assert store.get(unknown.evidence_id).created_at is None


def test_tied_activity_is_stable_across_insertion_orders_and_source_keys(tmp_path):
    identities = [("source-b", "same"), ("source-a", "z-last"), ("source-a", "same")]
    results = []
    for index, sequence in enumerate((identities, list(reversed(identities)))):
        store = EvidenceStore(tmp_path / str(index) / "evidence.sqlite3")
        records = {
            (source, conversation): put(store, conversation, source=source)
            for source, conversation in sequence
        }
        expected = [records[key].evidence_id for key in sorted(identities)]
        assert len(set(expected)) == 3
        for _ in range(2):
            assert store.list_conversation_seed_ids() == expected
            assert store.list_conversation_seed_ids(limit=2) == expected[:2]
        results.append(store.list_conversation_seed_ids())
    assert results[0] == results[1]


def test_seed_tie_uses_original_time_then_ordinal_then_evidence_id(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    put(store, "time", "later", created_at=datetime(2026, 9, 15, tzinfo=UTC))
    earliest = put(store, "time", "earlier", ordinal="z")
    put(store, "ordinal", "z", ordinal="z")
    ordinal = put(store, "ordinal", "a", ordinal="a")
    tied = [put(store, "id", message, ordinal="same") for message in ("second", "first")]

    assert store.list_conversation_seed_ids() == [
        earliest.evidence_id,
        min(record.evidence_id for record in tied),
        ordinal.evidence_id,
    ]


def test_offsets_are_ordered_by_instant_for_conversation_and_seed(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    put(
        store,
        "offset-turn",
        "later-instant",
        created_at=datetime.fromisoformat("2026-09-09T01:00:00+00:00"),
    )
    earlier_text = put(
        store,
        "offset-turn",
        "earlier-instant",
        created_at=datetime.fromisoformat("2026-09-09T08:00:00+08:00"),
    )

    assert [record.message_id for record in store.list_conversation(earlier_text.evidence_id)] == [
        "earlier-instant",
        "later-instant",
    ]
    assert store.list_conversation_seed_ids() == [earlier_text.evidence_id]


def test_search_conversations_orders_offsets_by_instant(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    put(
        store,
        "search-offset-turn",
        "search-later-instant",
        created_at=datetime.fromisoformat("2026-09-09T01:00:00+00:00"),
    )
    put(
        store,
        "search-offset-turn",
        "search-earlier-instant",
        created_at=datetime.fromisoformat("2026-09-09T08:00:00+08:00"),
    )

    hits = store.search_conversations("search-", user_id="local", limit=10)

    assert [record.message_id for record in hits] == [
        "search-later-instant",
        "search-earlier-instant",
    ]


def test_legacy_hydration_delegates_to_ids_and_hydrates_only_selected(tmp_path, monkeypatch):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    records = [put(store, name) for name in ("one", "two", "three")]
    limits = []
    hydrated = []
    hydrate = store._get_with_connection

    def select(*, limit=None):
        limits.append(limit)
        return [records[2].evidence_id, "missing", records[0].evidence_id]

    def observe(connection, evidence_id):
        hydrated.append(evidence_id)
        return hydrate(connection, evidence_id)

    monkeypatch.setattr(store, "list_conversation_seed_ids", select)
    monkeypatch.setattr(store, "_get_with_connection", observe)
    assert store.list_conversation_seeds() == [records[2], records[0]]
    assert limits == [10_000]
    assert hydrated == [records[2].evidence_id, "missing", records[0].evidence_id]


def test_legacy_seed_records_equal_ids_without_modifying_stored_rows(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    put(store, "one")
    put(store, "two")
    put(store, "one", "second")
    tables = ("evidence", "evidence_versions", "evidence_origins")

    def snapshot():
        with store._connect() as connection:
            return {
                table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")]
                for table in tables
            }

    before = snapshot()
    for limit in (1, 2, 100_000):
        ids = store.list_conversation_seed_ids(limit=limit)
        assert store.list_conversation_seeds(limit=limit) == [
            store.get(identifier) for identifier in ids
        ]
    assert snapshot() == before


def test_unlimited_ids_cover_conversations_beyond_old_cap(tmp_path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    count = 100_001
    # Bulk metadata fixture avoids measuring individual import transactions.
    with store._connect(writing=True) as connection:
        connection.executemany(
            "INSERT INTO evidence (evidence_id,idempotency_key,user_id,source_id,conversation_id,"
            "message_id,content_kind,role,written_by,created_at,verbatim,content_hash,revision,"
            "imported_at,updated_at) VALUES (?,?,'local','fixture',?,?,'raw_conversation',"
            "'user','person',?,'x','synthetic',1,?,?)",
            (
                (
                    f"e-{index:06d}",
                    f"key-{index}",
                    f"conversation-{index:06d}",
                    f"m-{index}",
                    AT.isoformat(),
                    AT.isoformat(),
                    AT.isoformat(),
                )
                for index in range(count)
            ),
        )

    ids = store.list_conversation_seed_ids()
    assert len(ids) == len(set(ids)) == count
    assert ids == [f"e-{index:06d}" for index in range(count)]
    assert store.list_conversation_seed_ids(limit=count) == ids
    assert store.list_conversation_seed_ids(limit=2) == ids[:2]


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "2"])
def test_id_limit_rejects_nonpositive_or_noninteger_values(tmp_path, limit):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    with pytest.raises(ValueError, match="positive integer"):
        store.list_conversation_seed_ids(limit=limit)


@pytest.mark.parametrize("limit", [0, -1, 100_001])
def test_legacy_limit_validation_is_unchanged(tmp_path, limit):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    with pytest.raises(ValueError, match="between 1 and 100000"):
        store.list_conversation_seeds(limit=limit)


def test_empty_store_returns_no_seeds(tmp_path: Path):
    store = EvidenceStore(tmp_path / "evidence.sqlite3")
    assert store.list_conversation_seed_ids() == []
    assert store.list_conversation_seed_ids(limit=1) == []
    assert store.list_conversation_seeds() == []
