"""Searching imported conversations before any generation model is configured.

This search is literal, not semantic, and that is a correction rather than a
shortcut. Literal conversation search must remain available before a model is
configured and independently of SDK semantic-memory extraction.
"""

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest

from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.storage import ConversationSearchUnavailableError, EvidenceStore


def message(
    *,
    verbatim: str,
    conversation_id: str = "conversation-1",
    message_id: str = "message-1",
    user_id: str = "local",
    source_id: str = "cursor",
    created_at: datetime | None = None,
) -> EvidenceInput:
    return EvidenceInput(
        user_id=user_id,
        source_id=source_id,
        conversation_id=conversation_id,
        message_id=message_id,
        role="user",
        written_by=WrittenBy.PERSON,
        created_at=created_at or datetime(2026, 9, 3, 1, 17, tzinfo=UTC),
        verbatim=verbatim,
        origin_meta={
            EvidenceOrigin.FILE: OriginMetadata(
                raw_meta={"schema": "fixture-v1"},
                source_version="fixture-1",
            )
        },
    )


def test_recent_conversations_follow_import_time_not_when_they_were_said(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(
        message(
            verbatim="先写进库的一句",
            message_id="first",
            created_at=datetime(2026, 9, 23, 6, 0, tzinfo=UTC),
        )
    )
    store.put(
        message(
            verbatim="后写进库的一句",
            message_id="second",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )

    hits, total = store.list_recent_conversations(user_id="local", limit=10, offset=0)

    assert total == 2
    assert [hit.message_id for hit in hits] == ["second", "first"]


def test_recent_activity_uses_message_time_and_skips_injected_context(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(
        message(
            verbatim="刚刚真实发送的消息",
            message_id="live",
            created_at=datetime(2026, 9, 23, 6, 0, tzinfo=UTC),
        )
    )
    store.put(
        message(
            verbatim="历史导入但刚写进库",
            message_id="history",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    )
    store.put(
        message(
            verbatim="<environment_context>internal context</environment_context>",
            message_id="injected",
            created_at=datetime(2026, 9, 23, 7, 0, tzinfo=UTC),
        )
    )

    hits, total = store.list_recent_conversations(
        user_id="local", limit=1, offset=0, recent_activity=True
    )

    assert total == 2
    assert [hit.message_id for hit in hits] == ["live"]


def test_recent_conversations_hydrate_a_page_with_constant_query_count(
    tmp_path, monkeypatch
) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    for index in range(30):
        store.put(
            message(
                verbatim=f"page item {index}",
                conversation_id=f"conversation-{index}",
                message_id=f"message-{index}",
            )
        )

    statements: list[str] = []
    connect = store._connect

    @contextmanager
    def traced_connection(**kwargs):
        with connect(**kwargs) as connection:
            connection.set_trace_callback(
                lambda sql: (
                    statements.append(sql) if sql.lstrip().upper().startswith("SELECT") else None
                )
            )
            yield connection

    with monkeypatch.context() as patch:
        patch.setattr(store, "_connect", traced_connection)
        hits, total = store.list_recent_conversations(user_id="local", limit=30, offset=0)

    assert total == 30
    assert len(hits) == 30
    assert len(statements) <= 4


def test_a_cursor_turn_also_filed_under_claude_code_is_listed_once_as_cursor(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(message(verbatim="开关还是打不开", conversation_id="cursor-chat", message_id="c"))
    store.put(
        message(
            verbatim="开关还是打不开",
            conversation_id="cursor-chat",
            message_id="replayed",
            source_id="claude-code",
        )
    )
    store.put(
        message(
            verbatim="真正在 Claude Code 里说的",
            conversation_id="claude-session",
            message_id="own",
            source_id="claude-code",
        )
    )

    hits, total = store.list_recent_conversations(user_id="local", limit=10, offset=0)

    assert total == 2
    assert sorted((hit.source_id, hit.message_id) for hit in hits) == [
        ("claude-code", "own"),
        ("cursor", "c"),
    ]


def test_cursor_replay_does_not_hide_a_same_named_claude_session_in_another_workspace(
    tmp_path,
) -> None:
    """Replay de-duplication must use the same workspace identity as reads."""
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(
        message(
            verbatim="cursor project A",
            conversation_id="same",
            message_id="cursor-a",
            source_id="cursor",
        ).model_copy(update={"workspace": "workspace:v1:project-a"})
    )
    store.put(
        message(
            verbatim="claude project B",
            conversation_id="same",
            message_id="claude-b",
            source_id="claude-code",
        ).model_copy(update={"workspace": "workspace:v1:project-b"})
    )

    hits, total = store.list_recent_conversations(user_id="local", limit=10)

    assert {(hit.source_id, hit.message_id) for hit in hits} == {
        ("cursor", "cursor-a"),
        ("claude-code", "claude-b"),
    }
    assert total == 2


def test_cursor_replay_count_uses_one_cursor_conversation_set(tmp_path) -> None:
    """The page count must not run the replay EXISTS once per evidence row."""
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    for index in range(40):
        store.put(
            message(
                verbatim=f"claude replay {index}",
                conversation_id="replayed",
                message_id=f"claude-{index}",
                source_id="claude-code",
            )
        )
    store.put(
        message(
            verbatim="the cursor original",
            conversation_id="replayed",
            message_id="cursor-original",
            source_id="cursor",
        )
    )
    store.put(
        message(
            verbatim="claude with no cursor copy",
            conversation_id="claude-only",
            message_id="claude-only",
            source_id="claude-code",
        )
    )

    hits, total = store.list_recent_conversations(user_id="local", limit=30)

    assert total == 2
    assert {hit.source_id for hit in hits} == {"cursor", "claude-code"}


def test_cursor_replay_count_matches_the_previous_exists_semantics(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    rows = [
        ("cursor", "paired"),
        ("claude-code", "paired"),
        ("claude-code", "paired"),
        ("claude-code", "unpaired"),
        ("codex", "paired"),
        ("codex", "other"),
    ]
    for index, (source, conversation) in enumerate(rows):
        store.put(
            message(
                verbatim=f"row {index}",
                source_id=source,
                conversation_id=conversation,
                message_id=f"m-{index}",
            )
        )

    with store._connect() as connection:
        previous = connection.execute(
            """
            SELECT COUNT(*) AS count
            FROM evidence
            WHERE user_id = 'local'
              AND NOT (
                source_id = 'claude-code'
                AND EXISTS (
                    SELECT 1 FROM evidence AS cursor_copy
                    WHERE cursor_copy.user_id = evidence.user_id
                      AND cursor_copy.source_id = 'cursor'
                      AND cursor_copy.conversation_id = evidence.conversation_id
                )
              )
            """
        ).fetchone()["count"]
    _hits, current = store.list_recent_conversations(user_id="local", limit=30)

    assert current == previous == 4


@pytest.mark.parametrize(
    "source_ids",
    [("codex",), ("cursor", "claude-code"), ("cursor", "claude-code", "codex")],
)
def test_cursor_replay_count_is_equivalent_with_source_filters(tmp_path, source_ids) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    for index, (source, conversation) in enumerate(
        [
            ("cursor", "paired"),
            ("claude-code", "paired"),
            ("claude-code", "unpaired"),
            ("codex", "paired"),
            ("codex", "other"),
        ]
    ):
        store.put(
            message(
                verbatim=f"row {index}",
                source_id=source,
                conversation_id=conversation,
                message_id=f"m-{index}",
            )
        )

    placeholders = ",".join("?" for _ in source_ids)
    with store._connect() as connection:
        previous = connection.execute(
            f"""
            SELECT COUNT(*) AS count
            FROM evidence
            WHERE user_id = 'local'
              AND source_id IN ({placeholders})
              AND NOT (
                source_id = 'claude-code'
                AND EXISTS (
                    SELECT 1 FROM evidence AS cursor_copy
                    WHERE cursor_copy.user_id = evidence.user_id
                      AND cursor_copy.source_id = 'cursor'
                      AND cursor_copy.conversation_id = evidence.conversation_id
                )
              )
            """,
            source_ids,
        ).fetchone()["count"]
    _hits, current = store.list_recent_conversations(
        user_id="local",
        limit=30,
        source_ids=source_ids,
    )

    assert current == previous


def test_an_empty_library_lists_no_recent_conversations(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")

    hits, total = store.list_recent_conversations(user_id="local", limit=10, offset=0)

    assert hits == []
    assert total == 0


def test_conversation_browser_groups_by_source_and_conversation(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    for source in ("cursor", "codex"):
        store.put(
            message(
                verbatim=f"{source} 的第一轮",
                source_id=source,
                conversation_id="shared-id",
                message_id=f"{source}-1",
            )
        )

    conversations, total = store.list_conversation_summaries(user_id="local")

    assert total == 2
    assert {(item.source_id, item.conversation_id) for item in conversations} == {
        ("cursor", "shared-id"),
        ("codex", "shared-id"),
    }


def test_conversation_turn_page_keeps_complete_recent_turns(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    seed_id = ""
    for turn in range(25):
        stored = store.put(
            message(
                verbatim=f"用户问题 {turn}",
                message_id=f"user-{turn:02d}",
                created_at=datetime(2026, 9, 3, 1, turn, tzinfo=UTC),
            )
        )
        if turn == 0:
            seed_id = stored.record.evidence_id
        assistant = message(
            verbatim=f"助手回答 {turn}",
            message_id=f"assistant-{turn:02d}",
            created_at=datetime(2026, 9, 3, 1, turn, 30, tzinfo=UTC),
        )
        store.put(assistant.model_copy(update={"role": "assistant", "written_by": "agent"}))

    recent = store.list_conversation_turns(seed_id, limit=20)
    older = store.list_conversation_turns(seed_id, offset=20, limit=20)
    summary = store.conversation_summary(seed_id)

    assert recent.total_turns == 25
    assert recent.turn_by_evidence_id[recent.records[0].evidence_id] == 6
    assert recent.turn_by_evidence_id[recent.records[-1].evidence_id] == 25
    assert len(recent.records) == 40
    assert older.total_turns == 25
    assert len(older.records) == 10
    assert older.turn_by_evidence_id[older.records[0].evidence_id] == 1
    assert summary is not None and summary.anchor_turn_offset == 24


def test_a_phrase_inside_a_message_finds_its_conversation(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(message(verbatim="昨天晚上重新打了一个包，抽取终于跑通了", message_id="m-1"))
    store.put(message(verbatim="周末想去爬山，天气应该不错", message_id="m-2"))

    hits = store.search_conversations("抽取终于", user_id="local", limit=10)

    assert [hit.message_id for hit in hits] == ["m-1"]


def test_a_query_shorter_than_the_trigram_floor_still_finds_its_rows(tmp_path) -> None:
    """Two Chinese characters is an ordinary query and must not fall through.

    The index is a trigram index, so it holds nothing shorter than three
    characters. Left at that, the commonest kind of Chinese query would come
    back empty and look exactly like "you never said that".
    """
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(message(verbatim="打包这件事今天终于收尾了", message_id="m-1"))
    store.put(message(verbatim="周末想去爬山", message_id="m-2"))

    hits = store.search_conversations("打包", user_id="local", limit=10)

    assert [hit.message_id for hit in hits] == ["m-1"]


def test_rows_imported_before_the_index_existed_are_reachable(tmp_path) -> None:
    """A library imported by an older build is the only library that exists.

    Every real user reaching this feature already has their history on disk;
    an index that only covers rows written after it was added would leave all
    of them searching an empty corpus.
    """
    database = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(database)
    store.put(message(verbatim="昨天晚上重新打了一个包，抽取终于跑通了", message_id="m-1"))

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE IF EXISTS evidence_search")
        connection.execute("DELETE FROM evidence_repairs WHERE name = 'conversation_search_index'")

    reopened = EvidenceStore(database)
    hits = reopened.search_conversations("抽取终于", user_id="local", limit=10)

    assert [hit.message_id for hit in hits] == ["m-1"]


def test_a_revised_message_is_found_by_its_new_words_and_not_its_old(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(message(verbatim="先用向量检索顶一阵子"))
    store.put(message(verbatim="改成字面检索，向量那条另开一项"))

    assert store.search_conversations("字面检索", user_id="local", limit=10)
    assert store.search_conversations("顶一阵子", user_id="local", limit=10) == []


def test_another_users_conversations_are_not_reachable(tmp_path) -> None:
    store = EvidenceStore(tmp_path / "one-memory.sqlite3")
    store.put(message(verbatim="抽取终于跑通了", user_id="local", message_id="m-1"))
    store.put(message(verbatim="抽取终于跑通了", user_id="someone-else", message_id="m-2"))

    hits = store.search_conversations("抽取终于", user_id="local", limit=10)

    assert [hit.user_id for hit in hits] == ["local"]


def test_a_missing_index_is_raised_rather_than_answered_as_no_matches(tmp_path) -> None:
    """The state a build without FTS5 lands in, and the one worth telling apart.

    Returning nothing here is what a corpus with no matches also returns, and
    that is the shape the packaging bug took last night: a core missing
    `onnxruntime` looked, from the interface, exactly like a user who had
    never mentioned the thing they searched for.
    """
    database = tmp_path / "one-memory.sqlite3"
    store = EvidenceStore(database)
    store.put(message(verbatim="抽取终于跑通了"))

    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE IF EXISTS evidence_search")

    with pytest.raises(ConversationSearchUnavailableError):
        store.search_conversations("抽取终于", user_id="local", limit=10)
