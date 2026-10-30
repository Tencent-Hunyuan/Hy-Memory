import asyncio
import json
import os
import sqlite3
import time
from pathlib import Path

from one_memory.imports.access import AuthorizedSourceFile, AuthorizedSourceRoot
from one_memory.imports.adapters import (
    ScanEvent,
    ScannedEvidence,
    ScanOptions,
    SourceCursor,
)
from one_memory.imports.jobs import ImportJobStore, ImportStatus
from one_memory.imports.models import EvidenceOrigin, WrittenBy
from one_memory.imports.runner import ImportRunner
from one_memory.imports.sources.claude_code import create_claude_code_adapter
from one_memory.imports.sources.codex import create_codex_adapter
from one_memory.imports.sources.cursor import CursorSourceAdapter
from one_memory.imports.sources.cursor_unified import CursorUnifiedSourceAdapter
from one_memory.imports.sources.parsing import is_host_generated_user_turn
from one_memory.imports.sources.workbuddy import create_workbuddy_adapter
from one_memory.imports.storage import EvidenceStore


def grant(source_id: str, root: Path) -> AuthorizedSourceRoot:
    return AuthorizedSourceRoot.grant(
        source_id=source_id,
        root=root,
        user_confirmed=True,
        home=root.parent / "other-home",
    )


def test_pipeline_json_is_host_generated_even_when_truncated() -> None:
    assert is_host_generated_user_turn(
        '{"request_id":"request","type":"write","steps":[{"detail":"truncated'
    )
    assert is_host_generated_user_turn(
        '{"success":true,"request_id":"request","reader":"client",'
        '"recommended_order":["READ_REQUEST"]}'
    )
    assert not is_host_generated_user_turn(
        '{"request_id":"my product request","note":"user-authored JSON"}'
    )


def test_workbuddy_record_parsing_never_blocks_the_event_loop(tmp_path) -> None:
    root = tmp_path / ".workbuddy" / "projects"
    transcript = root / "project" / "session.jsonl"
    write_jsonl(
        transcript,
        [{"role": "user", "sessionId": "session", "content": "hello"}],
    )
    adapter = create_workbuddy_adapter(grant("workbuddy", root))
    parse = adapter.parser

    def slow_parse(record, context):
        time.sleep(0.2)
        return parse(record, context)

    adapter.parser = slow_parse

    async def scenario() -> None:
        async def read_evidence() -> None:
            async for event in adapter.scan(ScanOptions()):
                if isinstance(event, ScannedEvidence):
                    return

        started = time.monotonic()
        consume = asyncio.create_task(read_evidence())
        await asyncio.sleep(0.02)
        assert time.monotonic() - started < 0.1
        await consume

    asyncio.run(scenario())


def test_cursor_store_user_info_envelope_is_not_attributed_to_the_person(tmp_path) -> None:
    """A Cursor store blob can be the host's session context, not the user's words.

    Real `chats/**/store.db` rows include a large `<user_info>` envelope followed
    by host tool catalog metadata with no `<user_query>`. It must not be stored
    as a person turn just because the transport row says `role=user`.
    """
    root = tmp_path / ".cursor"
    database = root / "chats" / "workspace" / "host-context" / "store.db"
    database.parent.mkdir(parents=True)
    host_context = (
        "<user_info>\n"
        "OS Version: darwin 24.1.0\n"
        "Workspace Path: /private/tmp/project\n"
        "</user_info>\n"
        "<dynamic_tools>host tool catalog</dynamic_tools>"
    )
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE blobs (id TEXT, data TEXT)")
    connection.execute(
        "INSERT INTO blobs (id, data) VALUES (?, ?)",
        ("host-context", json.dumps({"role": "user", "content": host_context})),
    )
    connection.commit()
    connection.close()

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert records == []


def test_cursor_store_timestamp_envelope_keeps_only_the_user_query_text(tmp_path) -> None:
    """Real Cursor chat blobs wrap the query in a host timestamp."""
    root = tmp_path / ".cursor"
    database = root / "chats" / "workspace" / "timestamped" / "store.db"
    database.parent.mkdir(parents=True)
    query = "Keep this request.\n```python\nprint('x < 5')\n```"
    content = (
        "<timestamp>Wednesday, Sep 23, 2026, 7:34 AM (UTC+8)</timestamp>\n"
        f"<user_query>\n{query}\n</user_query>"
    )
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE blobs (id TEXT, data TEXT)")
    connection.execute(
        "INSERT INTO blobs (id, data) VALUES (?, ?)",
        ("timestamped", json.dumps({"role": "user", "content": content})),
    )
    connection.commit()
    connection.close()

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert [item.evidence.verbatim for item in records] == [query]
    assert records[0].evidence.written_by is WrittenBy.PERSON
    meta = records[0].evidence.origin_meta[EvidenceOrigin.FILE].raw_meta
    assert meta["time_basis"] == "file_mtime"
    assert meta["turn_event_at"] == "2026-09-22T23:34:00+00:00"


def test_cursor_wal_change_token_does_not_become_a_future_conversation_time(
    tmp_path,
) -> None:
    root = tmp_path / ".cursor"
    database = root / "chats" / "workspace" / "wal-time" / "store.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE blobs (id TEXT, data TEXT)")
    connection.execute(
        "INSERT INTO blobs VALUES (?, ?)",
        ("message", json.dumps({"role": "assistant", "content": "answer"})),
    )
    connection.commit()
    stamp = 1_788_220_800
    os.utime(database, (stamp, stamp))
    try:
        records = evidence(
            asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root))))
        )
    finally:
        connection.close()

    assert len(records) == 1
    assert int(records[0].evidence.created_at.timestamp()) == stamp


def test_cursor_wal_store_resumes_from_its_rowid_after_new_frames(tmp_path) -> None:
    """The WAL change token marks a store changed; it must not reset its checkpoint."""
    root = tmp_path / ".cursor"
    database = root / "chats" / "workspace" / "wal-resume" / "store.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    connection.execute("CREATE TABLE blobs (id TEXT, data TEXT)")
    connection.execute(
        "INSERT INTO blobs VALUES (?, ?)",
        ("old", json.dumps({"id": "old", "role": "user", "content": "old row"})),
    )
    connection.commit()
    adapter = CursorUnifiedSourceAdapter(grant=grant("cursor", root))
    try:
        baseline = asyncio.run(adapter.live_baseline())
        assert evidence(asyncio.run(scan(adapter, cursors=baseline))) == []
        connection.execute(
            "INSERT INTO blobs VALUES (?, ?)",
            ("new", json.dumps({"id": "new", "role": "user", "content": "new row"})),
        )
        connection.commit()
        appended = asyncio.run(scan(adapter, cursors=baseline))
    finally:
        connection.close()

    assert [item.evidence.verbatim for item in evidence(appended)] == ["new row"]


def test_cursor_transcript_timestamp_envelope_keeps_only_the_user_query_text(tmp_path) -> None:
    root = tmp_path / ".cursor"
    transcript = (
        root / "projects" / "workspace" / "agent-transcripts" / "timestamped" / "timestamped.jsonl"
    )
    query = "Keep this transcript request."
    write_jsonl(
        transcript,
        [
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "<timestamp>Wednesday, Sep 23, 2026, 7:34 AM (UTC+8)"
                                f"</timestamp>\n<user_query>\n{query}\n</user_query>"
                            ),
                        }
                    ]
                },
            }
        ],
    )

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert [item.evidence.verbatim for item in records] == [query]
    assert records[0].evidence.written_by is WrittenBy.PERSON
    meta = records[0].evidence.origin_meta[EvidenceOrigin.FILE].raw_meta
    assert meta["time_basis"] == "file_mtime"
    assert meta["turn_event_at"] == "2026-09-22T23:34:00+00:00"


def test_cursor_transcript_uses_file_mtime_with_stable_record_order(tmp_path) -> None:
    root = tmp_path / ".cursor"
    transcript = root / "projects" / "workspace" / "agent-transcripts" / "chat" / "chat.jsonl"
    write_jsonl(
        transcript,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": "first"}]}},
            {
                "role": "assistant",
                "message": {"content": [{"type": "text", "text": "second"}]},
            },
        ],
    )
    stamp = 1_788_220_800
    os.utime(transcript, (stamp, stamp))

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert [item.evidence.verbatim for item in records] == ["first", "second"]
    assert all(item.evidence.created_at is not None for item in records)
    assert records[0].evidence.created_at < records[1].evidence.created_at
    assert int(records[0].evidence.created_at.timestamp()) == stamp


def test_cursor_image_file_envelope_is_host_context_not_user_text(tmp_path) -> None:
    """Image attachment paths precede the real query; they are not the message."""
    root = tmp_path / ".cursor"
    transcript = root / "projects" / "workspace" / "agent-transcripts" / "image" / "image.jsonl"
    query = "Explain this image."
    content = (
        "[Image]\n"
        "<image_files>\n"
        "The following images were provided by the user and saved to disk for future use:\n"
        "1. /Users/private/project/assets/image.png\n"
        "</image_files>\n"
        "<timestamp>Wednesday, Sep 23, 2026, 7:34 AM (UTC+8)</timestamp>\n"
        f"<user_query>\n{query}\n</user_query>"
    )
    write_jsonl(
        transcript,
        [
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": content}]},
            }
        ],
    )

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert [item.evidence.verbatim for item in records] == [query]
    assert records[0].evidence.written_by is WrittenBy.PERSON


def test_cursor_store_meta_agent_id_accepts_a_binary_value(tmp_path) -> None:
    """SQLite may return the meta value as bytes; identity must still be stable."""
    root = tmp_path / ".cursor"
    database = root / "chats" / "workspace" / "binary-meta" / "store.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE blobs (id TEXT, data TEXT)")
        connection.execute("CREATE TABLE meta (key TEXT, value BLOB)")
        connection.execute(
            "INSERT INTO meta VALUES (?, ?)",
            (
                "0",
                sqlite3.Binary(json.dumps({"agentId": "binary-agent"}).encode().hex().encode()),
            ),
        )
        connection.execute(
            "INSERT INTO blobs VALUES (?, ?)",
            ("row-0", json.dumps({"role": "user", "content": "binary meta message"})),
        )
        connection.commit()
    finally:
        connection.close()

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert [item.evidence.conversation_id for item in records] == ["binary-agent"]


def test_cursor_bare_image_prefixes_keep_only_the_user_query(tmp_path) -> None:
    """Some real Cursor records have `[Image]` markers with no <image_files> block."""
    root = tmp_path / ".cursor"
    database = root / "chats" / "workspace" / "bare-image" / "store.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    try:
        connection.execute("CREATE TABLE blobs (id TEXT, data TEXT)")
        connection.execute(
            "INSERT INTO blobs VALUES (?, ?)",
            (
                "row-0",
                json.dumps(
                    {
                        "role": "user",
                        "content": (
                            "[Image]\n[Image]\n<user_query>\n只保留这段真实问题\n</user_query>"
                        ),
                    }
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert [item.evidence.verbatim for item in records] == ["只保留这段真实问题"]
    assert records[0].evidence.written_by is WrittenBy.PERSON


async def scan(
    adapter,
    *,
    cursors: dict[str, SourceCursor] | None = None,
    max_messages: int | None = None,
    reuse_listing: bool = False,
) -> list[ScanEvent]:
    return [
        event
        async for event in adapter.scan(
            ScanOptions(
                cursors=cursors or {},
                max_messages=max_messages,
                reuse_listing=reuse_listing,
            )
        )
    ]


def evidence(events: list[ScanEvent]) -> list[ScannedEvidence]:
    return [event for event in events if isinstance(event, ScannedEvidence)]


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )


def test_claude_code_preserves_sidechain_authorship_and_redacts_tool_content(
    tmp_path,
) -> None:
    root = tmp_path / "claude"
    transcript = root / "project" / "session.jsonl"
    write_jsonl(
        transcript,
        [
            {
                "type": "user",
                "sessionId": "session",
                "uuid": "u1",
                "cwd": "/private/work/acme",
                "timestamp": "2026-09-01T00:00:00Z",
                "message": {"content": [{"type": "text", "text": "Ship safely"}]},
            },
            {
                "type": "user",
                "sessionId": "session",
                "uuid": "u2",
                "isSidechain": True,
                "message": {"content": [{"type": "text", "text": "Agent task"}]},
            },
            {
                "type": "user",
                "sessionId": "session",
                "uuid": "u3",
                "message": {
                    "content": "Visible request<system-reminder>HOST_SECRET</system-reminder>"
                },
            },
            {
                "type": "user",
                "sessionId": "session",
                "uuid": "u4",
                "toolUseResult": {"status": "generated"},
                "message": {"content": "Machine-created user-role record"},
            },
            {
                "type": "user",
                "sessionId": "session",
                "uuid": "tool",
                "message": {
                    "content": [{"type": "tool_result", "content": "API_KEY=do-not-import"}]
                },
            },
        ],
    )
    write_jsonl(
        root / "project" / "subagents" / "child.jsonl",
        [
            {
                "type": "user",
                "sessionId": "child",
                "uuid": "child-u1",
                "message": {"content": "Delegated task without sidechain flag"},
            }
        ],
    )

    records = evidence(asyncio.run(scan(create_claude_code_adapter(grant("claude-code", root)))))

    assert {item.evidence.message_id: item.evidence.written_by for item in records} == {
        "u1": WrittenBy.PERSON,
        "u2": WrittenBy.AGENT,
        "u3": WrittenBy.PERSON,
        "u4": WrittenBy.UNKNOWN,
        "tool": WrittenBy.TOOL,
        "child-u1": WrittenBy.AGENT,
    }
    assert all("do-not-import" not in item.evidence.verbatim for item in records)
    assert all("HOST_SECRET" not in item.evidence.verbatim for item in records)
    main = next(item.evidence for item in records if item.evidence.message_id == "u1")
    assert main.workspace
    assert "/private/work" not in main.workspace
    assert main.project == "acme"


TOOL_SECRET = "sk-abcdefghijklmnopqrstu"
TOOL_LIMIT = 1600 + len(" …[truncated]")


def test_claude_code_imports_tool_use_and_result_as_tool_evidence_not_the_person(
    tmp_path,
) -> None:
    """A tool_result rides in a `user` record, but the person did not write it.

    Anthropic's format puts tool_use blocks in assistant content and the
    matching tool_result blocks in user content. Reading the role off the
    record would store command output as the person's own words.
    """
    root = tmp_path / "claude"
    base = {"sessionId": "s", "cwd": "/private/work/acme"}
    write_jsonl(
        root / "project" / "s.jsonl",
        [
            {
                **base,
                "type": "user",
                "uuid": "u1",
                "message": {"role": "user", "content": "Run the tests"},
            },
            {
                **base,
                "type": "assistant",
                "uuid": "a-thinking",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "thinking", "thinking": "PRIVATE_THOUGHT"}],
                },
            },
            {
                **base,
                "type": "assistant",
                "uuid": "a-call",
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "Bash",
                            "input": {"command": f"pytest --token {TOOL_SECRET}"},
                        }
                    ],
                },
            },
            {
                **base,
                "type": "user",
                "uuid": "u-result",
                "toolUseResult": {"stdout": "3 failed"},
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": [{"type": "text", "text": "3 failed " + "x" * 5000}],
                        }
                    ],
                },
            },
            {
                **base,
                "type": "assistant",
                "uuid": "a-done",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "Done"}]},
            },
        ],
    )
    adapter = create_claude_code_adapter(grant("claude-code", root))

    records = [item.evidence for item in evidence(asyncio.run(scan(adapter)))]

    by_id = {item.message_id: item for item in records}
    assert list(by_id) == ["u1", "a-call", "u-result", "a-done"]
    call, result = by_id["a-call"], by_id["u-result"]
    assert [(item.role, item.written_by) for item in (call, result)] == [
        ("tool", WrittenBy.TOOL),
        ("tool", WrittenBy.TOOL),
    ]
    assert [item.message_id for item in records if item.written_by is WrittenBy.PERSON] == ["u1"]
    assert call.verbatim.startswith("[Bash] ")
    assert "pytest --token" in call.verbatim
    assert TOOL_SECRET not in call.verbatim
    assert result.verbatim.startswith("3 failed ")
    assert len(result.verbatim) <= TOOL_LIMIT
    assert result.verbatim.endswith("…[truncated]")
    assert call.workspace == by_id["u1"].workspace
    assert all("PRIVATE_THOUGHT" not in item.verbatim for item in records)
    again = [item.evidence for item in evidence(asyncio.run(scan(adapter)))]
    assert [(item.message_id, item.verbatim) for item in again] == [
        (item.message_id, item.verbatim) for item in records
    ]


def test_cursor_transcript_tool_use_is_tool_evidence_and_a_tool_result_is_not_the_person(
    tmp_path,
) -> None:
    root = tmp_path / ".cursor"
    write_jsonl(
        root / "projects" / "workspace" / "agent-transcripts" / "chat" / "chat.jsonl",
        [
            {
                "role": "user",
                "message": {
                    "content": [{"type": "text", "text": "<user_query>Fix it</user_query>"}]
                },
            },
            {
                "role": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Shell",
                            "input": {"command": f"deploy --key {TOOL_SECRET} " + "y" * 5000},
                        }
                    ]
                },
            },
            {
                "role": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "t1", "content": "deployed ok"}
                    ]
                },
            },
        ],
    )

    records = [
        item.evidence
        for item in evidence(
            asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root))))
        )
    ]

    assert [(item.role, item.written_by) for item in records] == [
        ("user", WrittenBy.PERSON),
        ("tool", WrittenBy.TOOL),
        ("tool", WrittenBy.TOOL),
    ]
    call, result = records[1], records[2]
    assert call.verbatim.startswith("[Shell] ")
    assert TOOL_SECRET not in call.verbatim
    assert len(call.verbatim) <= TOOL_LIMIT
    assert call.verbatim.endswith("…[truncated]")
    assert result.verbatim == "deployed ok"


def test_workbuddy_unwraps_user_query_and_does_not_call_subagent_user_human(
    tmp_path,
) -> None:
    root = tmp_path / "workbuddy"
    write_jsonl(
        root / "session.jsonl",
        [
            {
                "type": "user",
                "sessionId": "main",
                "uuid": "u1",
                "message": {
                    "content": "<system_reminder>hidden</system_reminder>"
                    "<user_query>Keep this</user_query>"
                },
            }
        ],
    )
    write_jsonl(
        root / "session" / "subagents" / "agent-1.jsonl",
        [
            {
                "type": "user",
                "sessionId": "child",
                "uuid": "u2",
                "message": {"content": "Delegated task"},
                "providerData": {"agent": "researcher"},
            }
        ],
    )

    records = evidence(asyncio.run(scan(create_workbuddy_adapter(grant("workbuddy", root)))))

    assert {item.evidence.verbatim: item.evidence.written_by for item in records} == {
        "Keep this": WrittenBy.PERSON,
        "Delegated task": WrittenBy.AGENT,
    }
    assert len(records) == 2


def test_workbuddy_receiver_agent_is_not_the_author(tmp_path) -> None:
    root = tmp_path / "workbuddy"
    write_jsonl(
        root / "session.jsonl",
        [
            {
                "type": "message",
                "role": "user",
                "id": "u1",
                "content": [{"type": "input_text", "text": "预算<5，这是用户要求。"}],
                "providerData": {"agent": "cli"},
            },
            {
                "type": "message",
                "role": "user",
                "id": "u2",
                "content": [{"type": "input_text", "text": "另一位主Agent接收的原话。"}],
                "providerData": {"agent": "custom-main"},
            },
            {
                "type": "message",
                "role": "user",
                "id": "u3",
                "content": [{"type": "input_text", "text": "机器委派输入。"}],
                "providerData": {"agent": "cli", "isSubAgent": True},
            },
        ],
    )
    records = evidence(asyncio.run(scan(create_workbuddy_adapter(grant("workbuddy", root)))))
    assert [item.evidence.written_by for item in records] == [
        WrittenBy.PERSON,
        WrittenBy.PERSON,
        WrittenBy.AGENT,
    ]
    assert records[0].evidence.verbatim == "预算<5，这是用户要求。"


def test_workbuddy_imports_real_input_and_output_text_blocks(tmp_path) -> None:
    root = tmp_path / "workbuddy"
    write_jsonl(
        root / "session.jsonl",
        [
            {
                "id": "u1",
                "timestamp": 1788164056837,
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "<system-reminder>hidden</system-reminder>"
                        "<user_query>记住我偏好本地模型</user_query>",
                    }
                ],
                "sessionId": "real-session",
            },
            {
                "id": "a1",
                "timestamp": 1788164057681,
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "已记录。"}],
                "sessionId": "real-session",
            },
        ],
    )

    records = evidence(asyncio.run(scan(create_workbuddy_adapter(grant("workbuddy", root)))))

    assert [item.evidence.verbatim for item in records] == [
        "记住我偏好本地模型",
        "已记录。",
    ]


def test_workbuddy_uses_sibling_metadata_for_workspace_scope(tmp_path) -> None:
    root = tmp_path / "workbuddy"
    transcript = root / "project" / "session.jsonl"
    write_jsonl(
        transcript,
        [
            {
                "id": "u1",
                "type": "message",
                "role": "user",
                "content": "Finish the project prototype",
                "sessionId": "session",
            }
        ],
    )
    transcript.with_name("session.meta.json").write_text(
        json.dumps(
            {
                "sourceConversationId": "session",
                "cwd": "/Users/example/projects/experience-square",
            }
        ),
        encoding="utf-8",
    )

    records = evidence(asyncio.run(scan(create_workbuddy_adapter(grant("workbuddy", root)))))

    assert records[0].evidence.workspace is not None
    assert records[0].evidence.project == "experience-square"
    assert "/Users/example" not in records[0].evidence.workspace


def test_codex_imports_redacted_tool_output_and_excludes_reasoning(
    tmp_path,
) -> None:
    root = tmp_path / "codex"
    write_jsonl(
        root / "2026" / "09" / "01" / "rollout-session.jsonl",
        [
            {
                "type": "response_item",
                "timestamp": "2026-09-01T00:59:00Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                "# AGENTS.md instructions for /repo\n"
                                "<INSTRUCTIONS>internal</INSTRUCTIONS>"
                            ),
                        }
                    ],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-01T00:59:30Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": (
                                '<codex_internal_context source="goal">'
                                "Continue working toward the active thread goal."
                                "</codex_internal_context>"
                            ),
                        }
                    ],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-09-01T01:00:00Z",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "Build the adapter"
                            "<system-reminder>HOST_SECRET</system-reminder>",
                        }
                    ],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "<heartbeat>internal scheduler output</heartbeat>",
                        }
                    ],
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "output": (
                        '[automation] {"message":"&lt;heartbeat&gt;internal&lt;/heartbeat&gt;"}'
                    ),
                },
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "output": "PASSWORD=do-not-import",
                },
            },
            {
                "type": "response_item",
                "payload": {"type": "reasoning", "summary": "private reasoning"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Done"}],
                },
            },
        ],
    )

    records = evidence(asyncio.run(scan(create_codex_adapter(grant("codex", root)))))

    assert [item.evidence.verbatim for item in records] == [
        "Build the adapter",
        "PASSWORD=<redacted-secret>",
        "Done",
    ]
    tool = records[1].evidence
    assert (tool.role, tool.written_by) == ("tool", WrittenBy.TOOL)
    assert all("do-not-import" not in item.evidence.verbatim for item in records)
    assert all("HOST_SECRET" not in item.evidence.verbatim for item in records)
    assert all("private reasoning" not in item.evidence.verbatim for item in records)


def test_workbuddy_imports_tool_name_and_a_clipped_redacted_result(tmp_path) -> None:
    """What the assistant did is part of the conversation; its full output is not.

    A name and a short, credential-free result is what a later reader needs to
    know a command ran and how it went, without importing whole files.
    """
    root = tmp_path / "workbuddy"
    write_jsonl(
        root / "session.jsonl",
        [
            {
                "type": "message",
                "role": "user",
                "id": "u1",
                "sessionId": "s",
                "content": "跑一下测试",
            },
            {
                "type": "function_call",
                "id": "c1",
                "sessionId": "s",
                "name": "run_terminal",
                "arguments": '{"command": "pytest --token sk-abcdefghijklmnopqrstu"}',
            },
            {
                "type": "function_call_result",
                "id": "r1",
                "sessionId": "s",
                "output": "3 failed " + "x" * 5000,
            },
        ],
    )

    records = evidence(asyncio.run(scan(create_workbuddy_adapter(grant("workbuddy", root)))))

    call, result = records[1].evidence, records[2].evidence
    assert [item.role for item in (call, result)] == ["tool", "tool"]
    assert {item.written_by for item in (call, result)} == {WrittenBy.TOOL}
    assert call.verbatim.startswith("[run_terminal] ")
    assert "sk-abcdefghijklmnopqrstu" not in call.verbatim
    assert result.verbatim.startswith("3 failed ")
    assert len(result.verbatim) <= 1600 + len(" …[truncated]")
    assert result.verbatim.endswith("…[truncated]")
    assert {item.conversation_id for item in (call, result)} == {
        records[0].evidence.conversation_id
    }


def test_cursor_reads_only_bubble_namespace_and_uses_subagent_headers(
    tmp_path,
) -> None:
    root = tmp_path / "Cursor" / "User"
    database = root / "globalStorage" / "state.vscdb"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    connection.execute(
        "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
        (
            "composer.composerHeaders",
            json.dumps(
                {
                    "allComposers": [
                        {"composerId": "main", "name": "Main conversation"},
                        {
                            "composerId": "child",
                            "name": "Background worker",
                            "subagentInfo": {"parentComposerId": "main"},
                        },
                    ]
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        (
            "bubbleId:main:m1",
            json.dumps({"type": 1, "text": "Human text", "createdAt": 1_788_220_800_000}),
        ),
    )
    connection.execute(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        (
            "bubbleId:child:m2",
            json.dumps({"type": 1, "text": "Parent agent instruction"}),
        ),
    )
    connection.execute(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        (
            "bubbleId:main:m3",
            json.dumps(
                {
                    "type": 1,
                    "text": (
                        "Briefly inform the user about the task result "
                        "and perform any follow-up actions (if needed)."
                    ),
                }
            ),
        ),
    )
    connection.execute(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        ("secretToken", json.dumps({"text": "must-not-read"})),
    )
    connection.commit()
    connection.close()

    records = evidence(asyncio.run(scan(CursorSourceAdapter(grant=grant("cursor", root)))))

    assert [item.evidence.verbatim for item in records] == [
        "Human text",
        "Parent agent instruction",
        (
            "Briefly inform the user about the task result "
            "and perform any follow-up actions (if needed)."
        ),
    ]
    assert [item.evidence.written_by for item in records] == [
        WrittenBy.PERSON,
        WrittenBy.AGENT,
        WrittenBy.AGENT,
    ]
    cursor = records[-1].cursor
    repeated = evidence(
        asyncio.run(
            scan(
                CursorSourceAdapter(grant=grant("cursor", root)),
                cursors={cursor.file_key: cursor},
            )
        )
    )
    assert repeated == []

    connection = sqlite3.connect(database)
    connection.execute(
        "DELETE FROM cursorDiskKV WHERE key IN ('bubbleId:child:m2', 'bubbleId:main:m3')"
    )
    connection.commit()
    connection.execute("VACUUM")
    connection.close()
    after_vacuum = evidence(
        asyncio.run(
            scan(
                CursorSourceAdapter(grant=grant("cursor", root)),
                cursors={cursor.file_key: cursor},
            )
        )
    )
    assert [item.evidence.verbatim for item in after_vacuum] == ["Human text"]
    assert all("must-not-read" not in item.evidence.verbatim for item in records)


def test_cursor_reads_subagent_headers_from_the_current_composer_headers_table(tmp_path) -> None:
    """Cursor 3.22.7 stores headers in a table, not the old ItemTable JSON key."""
    root = tmp_path / "Cursor" / "User"
    database = root / "globalStorage" / "state.vscdb"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE composerHeaders (
            composerId TEXT PRIMARY KEY,
            workspaceId TEXT,
            createdAt INTEGER,
            lastUpdatedAt INTEGER,
            isArchived INTEGER,
            isSubagent INTEGER,
            recency INTEGER,
            checkpointAt INTEGER,
            subagentTypeName TEXT,
            value TEXT
        );
        INSERT INTO composerHeaders
            (composerId, workspaceId, isArchived, isSubagent, subagentTypeName)
            VALUES ('child', 'workspace-a', 0, 1, 'background');
        """
    )
    connection.execute(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        (
            "bubbleId:child:m1",
            json.dumps({"type": 1, "text": "Child agent instruction"}),
        ),
    )
    connection.commit()
    connection.close()

    records = evidence(asyncio.run(scan(CursorSourceAdapter(grant=grant("cursor", root)))))

    assert [item.evidence.verbatim for item in records] == ["Child agent instruction"]
    assert records[0].evidence.written_by is WrittenBy.AGENT


def test_cursor_legacy_composer_headers_preserve_workspace_identity(tmp_path) -> None:
    """The global legacy DB is cross-workspace; composerId alone is not enough."""
    root = tmp_path / "Cursor" / "User"
    database = root / "globalStorage" / "state.vscdb"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.executescript(
        """
        CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE composerHeaders (
            composerId TEXT PRIMARY KEY,
            workspaceId TEXT,
            isArchived INTEGER,
            isSubagent INTEGER,
            subagentTypeName TEXT
        );
        INSERT INTO composerHeaders VALUES
            ('same-composer', 'workspace-a', 0, 0, NULL);
        """
    )
    connection.execute(
        "INSERT INTO cursorDiskKV VALUES (?, ?)",
        (
            "bubbleId:same-composer:m1",
            json.dumps({"type": 1, "text": "workspace a words"}),
        ),
    )
    connection.commit()
    connection.close()

    records = evidence(asyncio.run(scan(CursorSourceAdapter(grant=grant("cursor", root)))))

    assert len(records) == 1
    assert records[0].evidence.workspace == "workspace-a"


def test_cursor_unified_prefers_transcripts_and_reads_unrepresented_store_sessions(
    tmp_path,
) -> None:
    root = tmp_path / ".cursor"
    transcript = (
        root / "projects" / "workspace" / "agent-transcripts" / "represented" / "represented.jsonl"
    )
    write_jsonl(
        transcript,
        [
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "<user_query>My verified preference</user_query>",
                        }
                    ]
                },
            },
            {
                "role": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]},
            },
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                "Briefly inform the user about the task result "
                                "and perform any follow-up actions (if needed)."
                            ),
                        }
                    ]
                },
            },
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "<dynamic_tool_catalog>generated</dynamic_tool_catalog>",
                        }
                    ]
                },
            },
            {
                "role": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(
                                {
                                    "request_id": "pipeline-request",
                                    "type": "write",
                                    "total_ms": 1234,
                                    "steps": [
                                        {
                                            "step": "S1_L1_UPSERT",
                                            "status": "success",
                                        }
                                    ],
                                }
                            ),
                        }
                    ]
                },
            },
        ],
    )
    represented_store = root / "chats" / "workspace" / "represented" / "store.db"
    other_store = root / "chats" / "workspace" / "store-only" / "store.db"
    for database, text in (
        (represented_store, "duplicate"),
        (other_store, "Store-only request"),
    ):
        database.parent.mkdir(parents=True)
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE blobs (id TEXT, data TEXT)")
        connection.execute(
            "INSERT INTO blobs (id, data) VALUES (?, ?)",
            (
                f"{database.parent.name}-message",
                json.dumps(
                    {
                        "id": f"{database.parent.name}-message",
                        "role": "user",
                        "content": text,
                    }
                ),
            ),
        )
        connection.execute(
            "INSERT INTO blobs (id, data) VALUES (?, ?)",
            (
                f"{database.parent.name}-host",
                json.dumps(
                    {
                        "id": f"{database.parent.name}-host",
                        "role": "user",
                        "content": "<user_info>Host-generated context</user_info>",
                    }
                ),
            ),
        )
        connection.commit()
        connection.close()
    legacy = root / "User" / "globalStorage" / "state.vscdb"
    legacy.parent.mkdir(parents=True)
    connection = sqlite3.connect(legacy)
    connection.executescript(
        """
        CREATE TABLE cursorDiskKV (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT);
        """
    )
    connection.execute(
        "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
        (
            "composer.composerHeaders",
            json.dumps({"allComposers": [{"composerId": "represented"}]}),
        ),
    )
    connection.execute(
        "INSERT INTO cursorDiskKV (key, value) VALUES (?, ?)",
        (
            "bubbleId:represented:host",
            json.dumps(
                {
                    "type": 1,
                    "text": (
                        "Briefly inform the user about the task result "
                        "and perform any follow-up actions (if needed)."
                    ),
                }
            ),
        ),
    )
    connection.commit()
    connection.close()

    events = asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root))))
    records = evidence(events)

    assert [item.evidence.verbatim for item in records] == [
        "My verified preference",
        "[Read]",
        (
            "Briefly inform the user about the task result "
            "and perform any follow-up actions (if needed)."
        ),
        (
            '{"request_id": "pipeline-request", "type": "write", '
            '"total_ms": 1234, "steps": [{"step": "S1_L1_UPSERT", '
            '"status": "success"}]}'
        ),
        "Store-only request",
        (
            "Briefly inform the user about the task result "
            "and perform any follow-up actions (if needed)."
        ),
    ]
    assert [item.evidence.written_by for item in records] == [
        WrittenBy.PERSON,
        WrittenBy.TOOL,
        WrittenBy.AGENT,
        WrittenBy.AGENT,
        WrittenBy.PERSON,
        WrittenBy.AGENT,
    ]

    store_cursor = next(
        item.cursor for item in records if item.evidence.verbatim == "Store-only request"
    )
    assert store_cursor.checkpoint_anchor is not None
    connection = sqlite3.connect(other_store)
    connection.execute(
        "UPDATE blobs SET id = ?, data = ? WHERE rowid = 1",
        (
            "replacement-message",
            json.dumps(
                {
                    "id": "replacement-message",
                    "role": "user",
                    "content": "Replacement request",
                }
            ),
        ),
    )
    connection.commit()
    connection.close()

    rescanned = evidence(
        asyncio.run(
            scan(
                CursorUnifiedSourceAdapter(grant=grant("cursor", root)),
                cursors={store_cursor.file_key: store_cursor},
            )
        )
    )
    assert "Replacement request" in [item.evidence.verbatim for item in rescanned]


def test_cursor_transcripts_with_the_same_name_in_two_projects_are_both_kept(
    tmp_path,
) -> None:
    """A reused session filename under another project is still history.

    Discovery used to key transcripts by file name alone, so the newest copy
    hid the other before it was read. Keep both while preserving the existing
    conversation id and separating them by source ordinal.
    """
    root = tmp_path / ".cursor"
    for project, text in (
        ("project-a", "work from project a"),
        ("project-b", "work from project b"),
    ):
        write_jsonl(
            root / "projects" / project / "agent-transcripts" / "same" / "same.jsonl",
            [
                {
                    "role": "user",
                    "message": {"content": [{"type": "text", "text": text}]},
                }
            ],
        )

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert sorted(item.evidence.verbatim for item in records) == [
        "work from project a",
        "work from project b",
    ]
    assert len({item.evidence.conversation_id for item in records}) == 1
    assert len({item.evidence.source_ordinal for item in records}) == 2


def test_cursor_bounded_scans_reuse_the_authorized_file_listing(tmp_path, monkeypatch) -> None:
    root = tmp_path / ".cursor"
    transcript = root / "projects" / "project" / "agent-transcripts" / "session" / "session.jsonl"
    write_jsonl(
        transcript,
        [
            {"role": "user", "message": {"content": [{"type": "text", "text": f"row {i}"}]}}
            for i in range(4)
        ],
    )
    adapter = CursorUnifiedSourceAdapter(grant=grant("cursor", root))

    from one_memory.imports.sources import cursor_unified

    calls = 0
    original = cursor_unified._list_cursor_files

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(cursor_unified, "_list_cursor_files", counted)
    first = asyncio.run(scan(adapter, max_messages=2, reuse_listing=True))
    cursors = {
        event.cursor.file_key: event.cursor
        for event in first
        if getattr(event, "cursor", None) is not None
    }
    second = asyncio.run(scan(adapter, cursors=cursors, max_messages=2, reuse_listing=True))

    assert len(evidence(first)) == 2
    assert len(evidence(second)) == 2
    assert calls == 1, "a continued history slice must not walk the whole Cursor tree again"

    completed_cursors = {
        event.cursor.file_key: event.cursor.model_copy(update={"read_to_end": True})
        for event in second
        if getattr(event, "cursor", None) is not None
    }
    write_jsonl(
        root / "projects" / "project" / "agent-transcripts" / "new" / "new.jsonl",
        [{"role": "user", "message": {"content": [{"type": "text", "text": "new row"}]}}],
    )
    refreshed = asyncio.run(scan(adapter, cursors=completed_cursors, reuse_listing=True))

    assert "new row" in [item.evidence.verbatim for item in evidence(refreshed)]
    assert calls == 2, "an exhausted cached listing must refresh before history completes"


def test_cursor_live_baseline_skips_existing_history_but_reads_appends(tmp_path) -> None:
    root = tmp_path / ".cursor"
    transcript = root / "projects" / "project" / "agent-transcripts" / "session" / "session.jsonl"
    write_jsonl(
        transcript,
        [{"role": "user", "message": {"content": [{"type": "text", "text": "old row"}]}}],
    )
    adapter = CursorUnifiedSourceAdapter(grant=grant("cursor", root))

    baseline = asyncio.run(adapter.live_baseline())
    initial = asyncio.run(scan(adapter, cursors=baseline))
    assert evidence(initial) == []

    with transcript.open("a") as target:
        target.write(
            json.dumps(
                {"role": "user", "message": {"content": [{"type": "text", "text": "new row"}]}}
            )
            + "\n"
        )
    appended = asyncio.run(scan(adapter, cursors=baseline))
    assert [item.evidence.verbatim for item in evidence(appended)] == ["new row"]


def test_cursor_live_baseline_tracks_store_rowid_without_replaying_old_rows(tmp_path) -> None:
    root = tmp_path / ".cursor"
    database = root / "chats" / "workspace" / "session" / "store.db"
    database.parent.mkdir(parents=True)
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE blobs (id TEXT, data TEXT)")
    connection.execute(
        "INSERT INTO blobs VALUES (?, ?)",
        ("old", json.dumps({"id": "old", "role": "user", "content": "old row"})),
    )
    connection.commit()
    connection.close()
    adapter = CursorUnifiedSourceAdapter(grant=grant("cursor", root))

    baseline = asyncio.run(adapter.live_baseline())
    assert evidence(asyncio.run(scan(adapter, cursors=baseline))) == []

    connection = sqlite3.connect(database)
    connection.execute(
        "INSERT INTO blobs VALUES (?, ?)",
        ("new", json.dumps({"id": "new", "role": "user", "content": "new row"})),
    )
    connection.commit()
    connection.close()
    appended = asyncio.run(scan(adapter, cursors=baseline))
    assert [item.evidence.verbatim for item in evidence(appended)] == ["new row"]


def test_cursor_history_rotates_from_a_partial_large_file_to_an_unstarted_file(tmp_path) -> None:
    root = tmp_path / ".cursor"
    for session in ("newer", "older"):
        write_jsonl(
            root / "projects" / "project" / "agent-transcripts" / session / f"{session}.jsonl",
            [
                {
                    "role": "user",
                    "message": {"content": [{"type": "text", "text": f"{session}-{i}"}]},
                }
                for i in range(4)
            ],
        )
    newer = root / "projects" / "project" / "agent-transcripts" / "newer" / "newer.jsonl"
    older = root / "projects" / "project" / "agent-transcripts" / "older" / "older.jsonl"
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))
    adapter = CursorUnifiedSourceAdapter(grant=grant("cursor", root))

    first = asyncio.run(scan(adapter, max_messages=2, reuse_listing=True))
    cursors = {
        event.cursor.file_key: event.cursor
        for event in first
        if getattr(event, "cursor", None) is not None
    }
    second = asyncio.run(scan(adapter, cursors=cursors, max_messages=2, reuse_listing=True))

    assert [item.evidence.verbatim for item in evidence(first)] == ["newer-0", "newer-1"]
    assert [item.evidence.verbatim for item in evidence(second)] == ["older-0", "older-1"]


def test_history_rotation_finishes_small_files_before_large_partial_files(tmp_path) -> None:
    from one_memory.imports.sources.common import rotate_history_files

    def source_file(name: str, size: int) -> AuthorizedSourceFile:
        return AuthorizedSourceFile(
            source_id="fixture",
            root=tmp_path,
            path=tmp_path / name,
            relative_path=Path(name),
            max_bytes=400_000_000,
            file_identity=f"identity:{name}",
            size=size,
            modified_ns=1,
            changed_ns=1,
        )

    files = [
        source_file("large.jsonl", 350_000_000),
        source_file("small.jsonl", 2_000),
        source_file("medium.jsonl", 40_000),
    ]
    cursors = {
        files[0].safe_id: SourceCursor(
            file_key=files[0].safe_id,
            file_identity=files[0].file_identity,
            checkpoint="byte:100",
            observed_size=files[0].size,
            observed_modified_ns=files[0].modified_ns,
        ),
        files[2].safe_id: SourceCursor(
            file_key=files[2].safe_id,
            file_identity=files[2].file_identity,
            checkpoint="byte:100",
            observed_size=files[2].size,
            observed_modified_ns=files[2].modified_ns,
        ),
    }

    rotated = rotate_history_files(files, cursors)

    assert [item.relative_path.name for item in rotated] == [
        "small.jsonl",
        "medium.jsonl",
        "large.jsonl",
    ]


def test_cursor_subagent_name_does_not_mark_another_projects_main_session_agent(
    tmp_path,
) -> None:
    root = tmp_path / ".cursor"
    write_jsonl(
        root / "projects" / "project-a" / "agent-transcripts" / "same" / "subagents" / "same.jsonl",
        [
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "subagent work"}]},
            }
        ],
    )
    write_jsonl(
        root / "projects" / "project-b" / "agent-transcripts" / "same" / "same.jsonl",
        [
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "main work"}]},
            }
        ],
    )

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert sorted(item.evidence.verbatim for item in records) == ["main work", "subagent work"]
    assert {item.evidence.verbatim: item.evidence.written_by for item in records} == {
        "main work": WrittenBy.PERSON,
        "subagent work": WrittenBy.AGENT,
    }


def test_cursor_same_project_duplicate_transcripts_still_take_one_copy(
    tmp_path,
) -> None:
    """The project/session key must not turn a moved duplicate into two copies."""
    root = tmp_path / ".cursor"
    first = root / "projects" / "project-a" / "agent-transcripts" / "same" / "same.jsonl"
    second = root / "projects" / "project-a" / "agent-transcripts" / "archive" / "same.jsonl"
    write_jsonl(
        first,
        [{"role": "user", "message": {"content": [{"type": "text", "text": "older"}]}}],
    )
    write_jsonl(
        second,
        [{"role": "user", "message": {"content": [{"type": "text", "text": "newer"}]}}],
    )
    os.utime(first, ns=(1, 1))
    os.utime(second, ns=(2, 2))

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert [item.evidence.verbatim for item in records] == ["newer"]


def test_cursor_store_in_another_workspace_is_not_shadowed_by_transcript(
    tmp_path,
) -> None:
    """Same conversation name in different workspaces is not one copy."""
    root = tmp_path / ".cursor"
    write_jsonl(
        root / "projects" / "workspace-a" / "agent-transcripts" / "same" / "same.jsonl",
        [
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "transcript A"}]},
            }
        ],
    )
    store = root / "chats" / "workspace-b" / "same" / "store.db"
    store.parent.mkdir(parents=True)
    connection = sqlite3.connect(store)
    connection.execute("CREATE TABLE blobs (id TEXT, data TEXT)")
    connection.execute(
        "INSERT INTO blobs VALUES (?, ?)",
        (
            "store-b",
            json.dumps({"id": "store-b", "role": "user", "content": "store B"}),
        ),
    )
    connection.commit()
    connection.close()

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert sorted(item.evidence.verbatim for item in records) == ["store B", "transcript A"]
    assert {item.evidence.conversation_id for item in records} == {"same"}


def test_cursor_acp_store_keeps_its_workspace_identity(tmp_path) -> None:
    """ACP sessions have the same project/session identity as transcripts."""
    root = tmp_path / ".cursor"
    store = root / "acp-sessions" / "project-b" / "same" / "store.db"
    store.parent.mkdir(parents=True)
    connection = sqlite3.connect(store)
    connection.execute("CREATE TABLE blobs (id TEXT, data TEXT)")
    connection.execute(
        "INSERT INTO blobs VALUES (?, ?)",
        (
            "acp-b",
            json.dumps({"id": "acp-b", "role": "user", "content": "ACP project B"}),
        ),
    )
    connection.commit()
    connection.close()

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert [item.evidence.verbatim for item in records] == ["ACP project B"]
    from one_memory.imports.access import opaque_workspace_id

    assert records[0].evidence.workspace == opaque_workspace_id("cursor", "project-b")


def test_cursor_acp_and_transcript_same_name_stay_in_their_projects(tmp_path) -> None:
    """ACP and transcript copies are scoped by project, not by bare session name."""
    root = tmp_path / ".cursor"
    write_jsonl(
        root / "projects" / "project-a" / "agent-transcripts" / "same" / "same.jsonl",
        [
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": "transcript A"}]},
            }
        ],
    )
    store = root / "acp-sessions" / "project-b" / "same" / "store.db"
    store.parent.mkdir(parents=True)
    connection = sqlite3.connect(store)
    connection.execute("CREATE TABLE blobs (id TEXT, data TEXT)")
    connection.execute(
        "INSERT INTO blobs VALUES (?, ?)",
        ("acp-b", json.dumps({"id": "acp-b", "role": "user", "content": "acp B"})),
    )
    connection.commit()
    connection.close()

    records = evidence(asyncio.run(scan(CursorUnifiedSourceAdapter(grant=grant("cursor", root)))))

    assert sorted(item.evidence.verbatim for item in records) == ["acp B", "transcript A"]
    assert len({item.evidence.workspace for item in records}) == 2


def test_new_job_resumes_each_existing_file_cursor_including_older_file(
    tmp_path,
) -> None:
    root = tmp_path / "claude"
    old_file = root / "a.jsonl"
    newer_file = root / "z.jsonl"
    write_jsonl(
        old_file,
        [{"type": "user", "sessionId": "a", "uuid": "a1", "message": {"content": "A"}}],
    )
    write_jsonl(
        newer_file,
        [{"type": "user", "sessionId": "z", "uuid": "z1", "message": {"content": "Z"}}],
    )
    adapter = create_claude_code_adapter(grant("claude-code", root))
    database = tmp_path / "one-memory.sqlite3"
    evidence_store = EvidenceStore(database)
    job_store = ImportJobStore(database)
    runner = ImportRunner(evidence_store=evidence_store, job_store=job_store)

    first = job_store.create(user_id="local", source_id="claude-code")
    assert (
        asyncio.run(runner.run(adapter=adapter, job_id=first.job_id)).status
        is ImportStatus.COMPLETED
    )
    with old_file.open("a", encoding="utf-8") as output:
        output.write(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "a",
                    "uuid": "a2",
                    "message": {"content": "A2"},
                }
            )
            + "\n"
        )
    second = job_store.create(user_id="local", source_id="claude-code")
    completed = asyncio.run(runner.run(adapter=adapter, job_id=second.job_id))

    assert completed.status is ImportStatus.COMPLETED
    assert completed.stored == 1
    assert completed.merged == 0
    assert evidence_store.count() == 3
