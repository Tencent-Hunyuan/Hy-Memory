import asyncio
import json
import sqlite3
from functools import partial
from pathlib import Path

import pytest
import zstandard

from one_memory.imports.access import (
    AuthorizedSourceRoot,
    SourceAccessError,
    SourceAccessErrorCode,
)
from one_memory.imports.adapters import (
    ScanEvent,
    ScanFailure,
    ScanIssueCode,
    ScannedEvidence,
    ScanOptions,
    SourceAdapter,
    SourceCursor,
)
from one_memory.imports.models import WrittenBy
from one_memory.imports.sources import opencode as opencode_module
from one_memory.imports.sources.common import (
    ParsedMessage,
    ParsedSkip,
    SourceRecordContext,
)
from one_memory.imports.sources.deepseek_harness import (
    DEEPSEEK_HARNESS_DESCRIPTOR,
    create_deepseek_harness_adapter,
)
from one_memory.imports.sources.hermes import (
    HERMES_DESCRIPTOR,
    create_hermes_adapter,
    parse_hermes_rollout_record,
)
from one_memory.imports.sources.openclaw import (
    OPENCLAW_DESCRIPTOR,
    create_openclaw_adapter,
)
from one_memory.imports.sources.opencode import (
    OPENCODE_DESCRIPTOR,
    create_opencode_adapter,
)
from one_memory.imports.sources.pi import PI_DESCRIPTOR, create_pi_adapter
from one_memory.imports.sources.qwen_code import (
    QWEN_CODE_DESCRIPTOR,
    create_qwen_code_adapter,
    parse_qwen_code_record,
)
from one_memory.imports.sources.qwenwork import (
    QWENWORK_DESCRIPTOR,
    create_qwenwork_adapter,
    parse_qwenwork_record,
)
from one_memory.imports.sources.registry import build_source_catalog
from one_memory.imports.sqlite_access import open_sqlite_readonly

SECRET = "SECRET_DO_NOT_IMPORT"


def test_pi_and_qwenwork_keep_tool_and_sidechain_content_out_of_the_persons_words(
    tmp_path: Path,
) -> None:
    pi_root = tmp_path / "pi"
    pi_root.mkdir()
    _write_jsonl(
        pi_root / "session-a.jsonl",
        [
            {"type": "session", "id": "session-a", "cwd": "/private/project"},
            {
                "type": "message",
                "id": "pi-user",
                "message": {"role": "user", "content": "visible pi"},
            },
            {
                "type": "message",
                "id": "pi-tool",
                "message": {"role": "toolResult", "content": SECRET},
            },
        ],
    )
    qwen_root = tmp_path / "qwen"
    qwen_root.mkdir()
    _write_jsonl(
        qwen_root / "session-q.jsonl",
        [
            {
                "uuid": "q-human",
                "sessionId": "session-q",
                "message": {"role": "user", "content": "visible qwen"},
                "origin": {"kind": "human"},
            },
            {
                "uuid": "q-agent",
                "sessionId": "session-q",
                "message": {"role": "user", "content": SECRET},
                "origin": {"kind": "agent"},
            },
            {
                "uuid": "q-sidechain",
                "sessionId": "session-q",
                "isSidechain": True,
                "message": {"role": "user", "content": SECRET},
            },
            {
                "uuid": "q-assistant",
                "sessionId": "session-q",
                "message": {"role": "assistant", "content": "visible qwen assistant"},
            },
            {
                "uuid": "q-wrapper",
                "sessionId": "session-q",
                "message": {
                    "role": "user",
                    "content": "<system-reminder>SECRET_DO_NOT_IMPORT</system-reminder>",
                },
            },
        ],
    )

    pi_events = _scan(create_pi_adapter(_grant(PI_DESCRIPTOR.source_id, pi_root, tmp_path)))
    qwen_events = _scan(
        create_qwenwork_adapter(_grant(QWENWORK_DESCRIPTOR.source_id, qwen_root, tmp_path))
    )

    # A tool result is kept as tool evidence, never as the person's words.
    assert [
        (item.evidence.verbatim, item.evidence.written_by) for item in _evidence(pi_events)
    ] == [("visible pi", WrittenBy.PERSON), (SECRET, WrittenBy.TOOL)]
    assert _evidence_text(qwen_events) == ["visible qwen", "visible qwen assistant"]
    assert [item.evidence.written_by for item in _evidence(qwen_events)] == [
        WrittenBy.PERSON,
        WrittenBy.AGENT,
    ]
    assert SECRET not in repr(qwen_events)


def test_qwen_code_reads_only_the_transcript_beside_its_bookkeeping(
    tmp_path: Path,
) -> None:
    """`chats/` holds more than the conversation, and one of them is JSONL.

    Layout and filenames come from the installed bundle
    (`@qwen-code/qwen-code@0.23.0`): `defaultSessionScanner` accepts
    `^[0-9a-fA-F-]{32,36}\\.jsonl$`, and `rebuildFromSessionJsonl` reads the
    same directory while excluding `*.ledger.jsonl`. Reading the ledger as a
    conversation would import usage accounting as the user's words.
    """
    root = tmp_path / "qwen-code"
    session = "a1b2c3d4-0000-4000-8000-000000000001"
    chats = root / "-Users-example-work-atlas" / "chats"
    chats.mkdir(parents=True)
    _write_jsonl(
        chats / f"{session}.jsonl",
        [
            {
                "sessionId": session,
                "cwd": "/Users/example/work/atlas",
                "timestamp": "2026-09-05T10:00:00.000Z",
                "type": "user",
                "text": "visible qwen code",
                "sentToModel": True,
            },
            # Typed by the person, but a command rather than anything they said.
            {
                "sessionId": session,
                "timestamp": "2026-09-05T10:00:01.000Z",
                "type": "user",
                "text": "/model qwen3-coder-plus",
                "sentToModel": False,
            },
            # A Gemini CLI fork: the model's turn is typed `gemini` here.
            {
                "sessionId": session,
                "timestamp": "2026-09-05T10:00:02.000Z",
                "type": "gemini",
                "text": "visible model reply",
            },
            # Usage telemetry rides in the same file.
            {
                "sessionId": session,
                "timestamp": "2026-09-05T10:00:03.000Z",
                "type": "system",
                "subtype": "ui_telemetry",
                "systemPayload": {"uiEvent": {"secret": SECRET}},
            },
            # A sub-agent's turn, owned by the tool call that spawned it.
            {
                "sessionId": session,
                "timestamp": "2026-09-05T10:00:04.000Z",
                "type": "user",
                "parent_tool_use_id": "tool-1",
                "message": {"role": "user", "content": SECRET},
            },
            # The stream-json shape, where the text is one level down.
            {
                "sessionId": session,
                "timestamp": "2026-09-05T10:00:05.000Z",
                "type": "user",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "visible via message"}],
                },
            },
        ],
    )
    _write_jsonl(
        chats / f"{session}.ledger.jsonl",
        [{"sessionId": session, "type": "user", "text": SECRET}],
    )
    (chats / f"{session}.runtime.json").write_text("{}", encoding="utf-8")

    events = _scan(create_qwen_code_adapter(_grant(QWEN_CODE_DESCRIPTOR.source_id, root, tmp_path)))

    assert _evidence_text(events) == [
        "visible qwen code",
        "visible model reply",
        "visible via message",
    ]
    assert [item.evidence.written_by for item in _evidence(events)] == [
        WrittenBy.PERSON,
        WrittenBy.AGENT,
        WrittenBy.PERSON,
    ]
    # The record carrying `cwd` keeps a workspace, so the memory it produces
    # can be scoped to that project rather than landing everywhere.
    workspaces = [item.evidence.workspace for item in _evidence(events)]
    assert workspaces[0] is not None and workspaces[0].startswith("workspace:v1:")
    assert workspaces[1] is None
    assert SECRET not in repr(events)


@pytest.mark.parametrize("role", ["user", "assistant"])
def test_hermes_strips_native_provider_recall_context(role: str) -> None:
    context = SourceRecordContext(
        file_key="hermes",
        relative_path="sessions/session.jsonl",
        ordinal="1",
    )
    result = parse_hermes_rollout_record(
        {
            "type": "message",
            "role": role,
            "content": (
                "before\n<memhub_memory_context>\n"
                "Untrusted memory reference:\n"
                '[{"memory_id":"m1","content":"RECALLED_SECRET"}]\n'
                "</memhub_memory_context>\nafter"
            ),
            "sessionId": "session-A",
        },
        context,
    )
    assert isinstance(result, ParsedMessage)
    assert result.verbatim == "before\n\nafter"
    assert "RECALLED_SECRET" not in result.verbatim


def test_hermes_skips_a_persisted_pure_recall_block() -> None:
    context = SourceRecordContext(
        file_key="hermes",
        relative_path="sessions/session.jsonl",
        ordinal="1",
    )
    result = parse_hermes_rollout_record(
        {
            "type": "message",
            "role": "user",
            "content": (
                "<memhub_memory_context>\n"
                '[{"memory_id":"m1","content":"RECALLED_SECRET"}]\n'
                "</memhub_memory_context>"
            ),
            "sessionId": "session-A",
        },
        context,
    )
    assert isinstance(result, ParsedSkip)
    assert result.reason_code == ScanIssueCode.NO_TEXT


@pytest.mark.parametrize(
    ("role", "record_type", "provenance", "written_by"),
    [
        ("user", "user", "real_user", WrittenBy.PERSON),
        ("model", "assistant", "assistant_output", WrittenBy.AGENT),
    ],
)
def test_qwen_native_parts_preserve_visible_text_not_reasoning_or_tools(
    role: str, record_type: str, provenance: str, written_by: WrittenBy
) -> None:
    context = SourceRecordContext(file_key="k", relative_path="chats/s.jsonl", ordinal="1")
    result = parse_qwen_code_record(
        {
            "uuid": "native-1",
            "sessionId": "native",
            "type": record_type,
            "provenance": provenance,
            "timestamp": "2026-09-12T08:00:00Z",
            "message": {
                "role": role,
                "parts": [
                    {"text": "PRIVATE_THOUGHT", "thought": True},
                    {"functionResponse": {"response": {"output": SECRET}}},
                    {"functionCall": {"name": "example", "args": {"value": SECRET}}},
                    {"text": "预算<5；尚未批准。"},
                    {"text": "保留后续可见回复。", "thought": False},
                ],
            },
        },
        context,
    )
    assert isinstance(result, ParsedMessage)
    assert result.verbatim == "预算<5；尚未批准。\n保留后续可见回复。"
    assert result.written_by == written_by
    assert result.role == ("user" if written_by is WrittenBy.PERSON else "assistant")
    assert result.message_id == "native-1"
    assert result.created_at is not None
    assert SECRET not in result.verbatim
    assert "PRIVATE_THOUGHT" not in repr(result)
    # The model's own calls in the same record become the next row, as tool evidence;
    # a user record's parts are never read as tool calls.
    if written_by is WrittenBy.PERSON:
        assert result.followed_by == ()
    else:
        (tool,) = result.followed_by
        assert (tool.role, tool.written_by, tool.message_id) == (
            "tool",
            WrittenBy.TOOL,
            "native-1:tool",
        )
        assert "[example]" in tool.verbatim


@pytest.mark.parametrize("provenance", ["tool_result", "system", "unknown_machine"])
def test_qwen_nonhuman_provenance_cannot_be_imported_as_user(provenance: str) -> None:
    context = SourceRecordContext(file_key="k", relative_path="chats/s.jsonl", ordinal="1")
    result = parse_qwen_code_record(
        {
            "type": "user",
            "provenance": provenance,
            "message": {
                "role": "user",
                "content": SECRET,
                "parts": [{"text": SECRET}],
            },
        },
        context,
    )
    assert isinstance(result, ParsedSkip)
    assert SECRET not in repr(result)


def test_qwen_code_and_qwen_work_stay_separate_sources(tmp_path: Path) -> None:
    """Two products, two directories, two source IDs.

    They were one for a while — recall installed into Qwen Code while the
    source list named Qwen Work — and a shared ID would have filed one
    product's conversations under the other's name.
    """
    assert QWEN_CODE_DESCRIPTOR.source_id != QWENWORK_DESCRIPTOR.source_id
    assert QWEN_CODE_DESCRIPTOR.display_name == "Qwen Code"
    assert QWENWORK_DESCRIPTOR.display_name == "Qwen Work"

    # Qwen Work states authorship in the record; Qwen Code does not have that
    # field, so the same fixture must not be readable by both parsers.
    qwen_work_record = {
        "uuid": "q-agent",
        "sessionId": "s",
        "message": {"role": "user", "content": "machine wrote this"},
        "origin": {"kind": "agent"},
    }
    context = SourceRecordContext(
        file_key="k",
        relative_path="chats/s.jsonl",
        ordinal="1",
    )
    assert isinstance(parse_qwenwork_record(qwen_work_record, context), ParsedSkip)
    # Qwen Code's parser has no `origin` to consult, which is exactly why it
    # cannot stand in for Qwen Work's.
    assert isinstance(parse_qwen_code_record(qwen_work_record, context), ParsedMessage)


def test_pi_takes_its_workspace_from_the_session_header(tmp_path: Path) -> None:
    """The working directory is stated once at the top and never repeated.

    Read per-message it is always absent, so every Pi memory lands with no
    workspace and no project — the user cannot tell which of their projects a
    recalled fact came from. A resumed scan starts past that header, so the
    header has to be read independently of the cursor rather than remembered
    from the previous batch.
    """
    root = tmp_path / "pi"
    root.mkdir()
    _write_jsonl(
        root / "session-h.jsonl",
        [
            {
                "type": "session",
                "id": "session-h",
                "cwd": "/Users/example/work/atlas",
            },
            {
                "type": "message",
                "id": "pi-first",
                "message": {"role": "user", "content": [{"type": "text", "text": "first"}]},
            },
            {
                "type": "message",
                "id": "pi-second",
                "message": {"role": "user", "content": [{"type": "text", "text": "second"}]},
            },
        ],
    )
    adapter = partial(create_pi_adapter, _grant(PI_DESCRIPTOR.source_id, root, tmp_path))

    first = _scan(adapter())
    assert _evidence_text(first) == ["first", "second"]
    projects = {item.evidence.project for item in _evidence(first)}
    assert projects == {"atlas"}
    workspaces = {item.evidence.workspace for item in _evidence(first)}
    assert workspaces != {None}

    # Resuming mid-file must still attribute the workspace, not lose it.
    partial_scan = _scan(adapter(), cursors={})
    resume_after_first = {
        event.cursor.file_key: event.cursor
        for event in partial_scan[: partial_scan.index(_evidence(partial_scan)[1])]
        if getattr(event, "cursor", None) is not None
    }
    resumed = _scan(adapter(), cursors=resume_after_first)
    assert _evidence_text(resumed) == ["second"]
    assert {item.evidence.project for item in _evidence(resumed)} == {"atlas"}


def test_a_scheduled_pipe_is_not_the_person_speaking(tmp_path: Path) -> None:
    """Pi runs unattended for automations, and those turns wear the user role.

    On this machine six of eight `user` turns came from an hourly tracker pipe
    and Pi's own title generator, not from the person. They reach extraction
    like any prompt, so without this the system could learn "facts about the
    user" from a robot on a timer. The session header's working directory is
    what separates them: an automation runs in its own directory, a person runs
    in a project. Per decision D-17 they are kept as agent turns rather than
    dropped, so the record survives without being read as self-description.
    """
    root = tmp_path / "pi"
    root.mkdir()

    def session(name: str, cwd: str, text: str) -> None:
        _write_jsonl(
            root / f"{name}.jsonl",
            [
                {"type": "session", "id": name, "cwd": cwd},
                {
                    "type": "message",
                    "id": f"{name}-turn",
                    "message": {"role": "user", "content": [{"type": "text", "text": text}]},
                },
            ],
        )

    session(
        "pipe",
        "/Users/example/.screenpipe/pipes/hourly-activity-tracker",
        "Do the work described above now.",
    )
    session("title", "/Users/example/.screenpipe/pi-title", "Generate a short chat title.")
    # A real chat lives in a project directory, and must stay attributed to the
    # person — otherwise this rule would silence the source it exists to protect.
    session("chat", "/Users/example/work/atlas", "create a pipe that tracks my hours")

    events = _scan(create_pi_adapter(_grant(PI_DESCRIPTOR.source_id, root, tmp_path)))

    spoken = {item.evidence.verbatim: item.evidence.written_by for item in _evidence(events)}
    # Nobody in an automated session is the person, so neither role of it is
    # imported. Qwen Work already held this line for the same situation, and
    # unifying toward the stricter side is what keeps a machine's words from
    # being stored at all rather than merely kept out of memory (decision D-17).
    assert spoken == {"create a pipe that tracks my hours": WrittenBy.PERSON}
    # And the reason a user reads has to say what happened: these turns were not
    # "internal reasoning", they were somebody else's words.
    assert ScanIssueCode.NOT_WRITTEN_BY_PERSON in _skip_codes(events)


def test_pi_does_not_import_a_sessions_subagent_transcripts(tmp_path: Path) -> None:
    """A sub-agent transcript is not a conversation the person had.

    Oh My Pi keeps the parent session in `<cwd>/<session>.jsonl` and writes
    each sub-agent's transcript into `<cwd>/<session>/<SubAgent>.jsonl`. In
    the child, `user` is the parent agent assigning the task, so importing it
    attributes a robot's instructions to the person.
    """
    root = tmp_path / "pi"
    root.mkdir()
    parent_text = "plan the migration"
    subagent_text = "do the migration subtask"
    _write_jsonl(
        root / "session-a.jsonl",
        [
            {"type": "session", "id": "session-a", "cwd": "/work/atlas"},
            {
                "type": "message",
                "id": "parent-turn",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": parent_text}],
                },
            },
        ],
    )
    artifact = root / "session-a"
    artifact.mkdir()
    _write_jsonl(
        artifact / "explorer.jsonl",
        [
            {"type": "session", "id": "explorer", "cwd": "/work/atlas"},
            {
                "type": "message",
                "id": "subagent-turn",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": subagent_text}],
                },
            },
        ],
    )
    # A directory whose name does not belong to a session file is ordinary
    # nested content and must keep importing.
    loose = root / "notes"
    loose.mkdir()
    loose_text = "a note kept in a subdirectory"
    _write_jsonl(
        loose / "note.jsonl",
        [
            {"type": "session", "id": "note", "cwd": "/work/atlas"},
            {
                "type": "message",
                "id": "note-turn",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": loose_text}],
                },
            },
        ],
    )

    events = _scan(create_pi_adapter(_grant(PI_DESCRIPTOR.source_id, root, tmp_path)))

    assert set(_evidence_text(events)) == {parent_text, loose_text}
    assert subagent_text not in _evidence_text(events)


def test_deepseek_zstd_keeps_only_explicit_human_and_assistant(tmp_path: Path) -> None:
    root = tmp_path / "dsh"
    session = root / "run-a"
    session.mkdir(parents=True)
    records = [
        {"type": "session", "id": "run-a", "cwd": "/private/project"},
        {
            "type": "user/message",
            "seq": 1,
            "data": {
                "id": "human",
                "role": "user",
                "source": {"kind": "user"},
                "content": [{"type": "text", "text": "visible human"}],
            },
        },
        {
            "type": "user/message",
            "seq": 2,
            "data": {
                "id": "harness",
                "role": "user",
                "source": {"kind": "environment"},
                "content": [{"type": "text", "text": SECRET}],
            },
        },
        {
            "type": "assistant/message",
            "seq": 3,
            "data": {
                "message": {
                    "id": "assistant",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "visible assistant"}],
                }
            },
        },
    ]
    payload = "".join(f"{json.dumps(record)}\n" for record in records).encode()
    (session / "session.jsonl.zstd").write_bytes(zstandard.ZstdCompressor().compress(payload))

    events = _scan(
        create_deepseek_harness_adapter(
            _grant(DEEPSEEK_HARNESS_DESCRIPTOR.source_id, root, tmp_path)
        )
    )

    assert _evidence_text(events) == ["visible human", "visible assistant"]
    assert SECRET not in repr(events)
    # Harness names the session's cwd once, in the header line, and never
    # again. Reading it per-record leaves every turn with no project, which is
    # how a real session imported before the header was carried forward.
    assert {event.evidence.project for event in events if hasattr(event, "evidence")} == {"project"}


def test_hermes_attributes_a_person_only_for_a_session_a_person_opened(
    tmp_path: Path,
) -> None:
    """`sessions.source` is the only structured author evidence Hermes keeps.

    Its own vocabulary mixes origins a person types at (`cli`, `tui`, `webui`,
    `desktop`) with origins the machine opens (`cron` for the scheduler,
    `delegate` for subagent hand-offs). Both store the instruction under the
    `user` role, so the role alone cannot tell them apart — which is why this
    reads the session rather than the message.
    """
    root = tmp_path / "hermes"
    root.mkdir()
    connection = sqlite3.connect(root / "state.db")
    connection.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, cwd TEXT);
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
            timestamp INTEGER, tool_calls TEXT, active INTEGER
        );
        INSERT INTO sessions VALUES
            ('typed', 'cli', '/Users/example/work/ledger'),
            ('scheduled', 'cron', '/Users/example/work/ledger');
        INSERT INTO messages VALUES
            (1, 'typed', 'user', 'a person typed this', 1000, NULL, 1),
            (2, 'scheduled', 'user', SECRET_PLACEHOLDER, 2000, NULL, 1);
        """.replace("SECRET_PLACEHOLDER", f"'{SECRET}'")
    )
    connection.close()

    events = _scan(create_hermes_adapter(_grant(HERMES_DESCRIPTOR.source_id, root, tmp_path)))
    attributed = {
        event.evidence.verbatim: event.evidence.written_by
        for event in events
        if hasattr(event, "evidence")
    }

    assert attributed == {
        "a person typed this": WrittenBy.PERSON,
        SECRET: WrittenBy.UNKNOWN,
    }
    # The scheduler's turn is still imported, so it keeps the conversation
    # readable, but only the typed one can become a memory.
    assert {event.evidence.project for event in events if hasattr(event, "evidence")} == {"ledger"}


def test_hermes_state_db_marks_user_unknown_and_resumes(tmp_path: Path) -> None:
    root = tmp_path / "hermes"
    root.mkdir()
    connection = sqlite3.connect(root / "state.db")
    connection.executescript(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
            timestamp INTEGER, tool_calls TEXT, active INTEGER
        );
        INSERT INTO messages VALUES
            (1, 'session-h', 'user', 'visible hermes', 1000, NULL, 1),
            (2, 'session-h', 'system', 'SECRET_DO_NOT_IMPORT', 2000, NULL, 1);
        """
    )
    connection.close()

    events = _scan(create_hermes_adapter(_grant(HERMES_DESCRIPTOR.source_id, root, tmp_path)))
    evidence = _evidence(events)

    assert [item.evidence.verbatim for item in evidence] == ["visible hermes"]
    assert evidence[0].evidence.written_by is WrittenBy.UNKNOWN
    assert SECRET not in repr(events)
    assert not _evidence(
        _scan(
            create_hermes_adapter(_grant(HERMES_DESCRIPTOR.source_id, root, tmp_path)),
            cursors=_cursors(events),
        )
    )


def test_opencode_reads_only_text_parts(tmp_path: Path) -> None:
    root = tmp_path / "opencode"
    root.mkdir()
    connection = sqlite3.connect(root / "opencode.db")
    connection.executescript(
        """
        CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT);
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, time_created INTEGER, data TEXT
        );
        INSERT INTO session VALUES ('session-o', '/private/project');
        INSERT INTO message VALUES
            ('message-o', 'session-o', 1000, '{"role":"user"}');
        INSERT INTO part VALUES
            ('part-text', 'message-o', 1000, '{"type":"text","text":"visible opencode"}'),
            ('part-tool', 'message-o', 1001, '{"type":"tool","text":"SECRET_DO_NOT_IMPORT"}'),
            ('part-first', 'message-o', 999, '{"type":"text","text":"first opencode"}');
        """
    )
    connection.close()

    events = _scan(create_opencode_adapter(_grant(OPENCODE_DESCRIPTOR.source_id, root, tmp_path)))

    assert _evidence_text(events) == ["first opencode\nvisible opencode"]
    assert _evidence(events)[0].evidence.written_by is WrittenBy.UNKNOWN
    assert SECRET not in repr(events)
    assert not _evidence(
        _scan(
            create_opencode_adapter(_grant(OPENCODE_DESCRIPTOR.source_id, root, tmp_path)),
            cursors=_cursors(events),
        )
    )


def test_locked_source_reports_busy_and_loses_nothing_on_retry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A host holding its own database open must postpone us, not derail us.

    This is the normal case, not an exotic one: the user is using the app we
    are importing from. Being locked out has to read as "come back later"
    rather than as an empty or half-read source.
    """
    root = tmp_path / "opencode"
    root.mkdir()
    connection = sqlite3.connect(root / "opencode.db")
    connection.executescript(
        """
        CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT);
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, time_created INTEGER, data TEXT
        );
        INSERT INTO session VALUES ('session-b', '/private/project');
        INSERT INTO message VALUES
            ('message-b', 'session-b', 1000, '{"role":"user"}');
        INSERT INTO part VALUES
            ('part-b', 'message-b', 1000, '{"type":"text","text":"written while busy"}');
        """
    )
    connection.commit()
    connection.close()

    # Only shorten the wait; the lock, the error and the handling are all real.
    impatient = partial(open_sqlite_readonly, timeout=0.05)
    monkeypatch.setattr(opencode_module, "open_sqlite_readonly", impatient)

    holder = sqlite3.connect(root / "opencode.db")
    holder.execute("BEGIN EXCLUSIVE")
    try:
        blocked = _scan(
            create_opencode_adapter(_grant(OPENCODE_DESCRIPTOR.source_id, root, tmp_path))
        )
    finally:
        holder.rollback()
        holder.close()

    assert [event.reason_code for event in blocked if isinstance(event, ScanFailure)] == [
        ScanIssueCode.SOURCE_BUSY
    ]
    assert not _evidence(blocked)

    # Resuming from the blocked attempt must still see the message it never read.
    retried = _scan(
        create_opencode_adapter(_grant(OPENCODE_DESCRIPTOR.source_id, root, tmp_path)),
        cursors=_cursors(blocked),
    )
    assert _evidence_text(retried) == ["written while busy"]


def test_openclaw_is_closed_before_its_conversation_reader_runs(tmp_path: Path) -> None:
    """The paused source must fail before opening even a valid conversation DB."""
    root = tmp_path / "openclaw"
    root.mkdir()
    conversation = sqlite3.connect(root / "conversations.db")
    conversation.executescript(
        """
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY, workspace_path TEXT, git_root TEXT
        );
        CREATE TABLE messages (
            id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT,
            content TEXT, created_at TEXT
        );
        INSERT INTO conversations VALUES ('session-c', '/private/project', NULL);
        INSERT INTO messages VALUES
            ('human-c', 'session-c', 'user', 'visible openclaw', '2026-09-01'),
            ('system-c', 'session-c', 'system', 'SECRET_DO_NOT_IMPORT', '2026-09-01');
        """
    )
    conversation.close()
    memory = sqlite3.connect(root / "memory.sqlite")
    memory.executescript(
        """
        CREATE TABLE chunks (id TEXT, session_key TEXT, role TEXT, content TEXT);
        INSERT INTO chunks VALUES
            ('memory-1', 'session-c', 'user', 'memory content is not chat evidence');
        """
    )
    memory.close()

    with pytest.raises(SourceAccessError) as refused:
        create_openclaw_adapter(_grant(OPENCLAW_DESCRIPTOR.source_id, root, tmp_path))
    assert refused.value.code is SourceAccessErrorCode.GRANT_REQUIRED


def test_catalog_registers_every_source_and_secondary_defaults(tmp_path: Path) -> None:
    catalog = build_source_catalog(home=tmp_path, environment={}, platform="darwin")

    assert set(catalog) == {
        "workbuddy",
        "cursor",
        "claude-code",
        "codex",
        "pi",
        "qwenwork",
        # The plan's ten plus Qwen Code, which recall had been installed into
        # for days while nothing read its conversations — it had been taken for
        # Qwen Work, and it is a different product.
        "qwen-code",
        "hermes",
        "deepseek-harness",
        "opencode",
        "openclaw",
    }
    assert catalog["pi"].candidate.path == tmp_path / ".pi/agent/sessions"
    assert catalog["qwenwork"].candidate.path == tmp_path / ".qwenworkcn/projects"
    assert catalog["qwen-code"].candidate.path == tmp_path / ".qwen/projects"
    assert catalog["hermes"].candidate.path == tmp_path / ".hermes"
    assert catalog["deepseek-harness"].candidate.path == tmp_path / ".dsh/sessions"
    assert catalog["opencode"].candidate.path == tmp_path / ".local/share/opencode"
    assert catalog["openclaw"].candidate.path == tmp_path / ".openclaw"


TOOL_SECRET = "sk-abcdefghijklmnopqrstu"
TOOL_LIMIT = 1600 + len(" …[truncated]")
THOUGHT = "PRIVATE_THOUGHT"


def _assert_tool_rows(
    events: list[ScanEvent], *, name: str | None
) -> tuple[ScannedEvidence, ScannedEvidence]:
    """The call names the tool and loses its secret; the result is clipped.

    Neither may be filed as the person's words, and reasoning stays out.
    """
    tools = [item for item in _evidence(events) if item.evidence.role == "tool"]
    assert len(tools) == 2
    call, result = tools
    assert {item.evidence.written_by for item in tools} == {WrittenBy.TOOL}
    if name is not None:
        assert call.evidence.verbatim.startswith(f"[{name}] ")
    assert "pytest --token" in call.evidence.verbatim
    assert TOOL_SECRET not in call.evidence.verbatim
    assert "3 failed " in result.evidence.verbatim
    assert len(result.evidence.verbatim) <= TOOL_LIMIT
    assert result.evidence.verbatim.endswith("…[truncated]")
    assert all(item.evidence.message_id for item in tools)
    assert THOUGHT not in repr(events)
    return call, result


def test_pi_imports_tool_call_and_result_as_tool_evidence(tmp_path: Path) -> None:
    """Pi's own shapes: `toolCall` blocks in assistant content, `toolResult` messages."""
    root = tmp_path / "pi"
    root.mkdir()
    _write_jsonl(
        root / "session-t.jsonl",
        [
            {"type": "session", "id": "session-t", "cwd": "/Users/example/work/atlas"},
            {
                "type": "message",
                "id": "pi-user",
                "message": {"role": "user", "content": [{"type": "text", "text": "run tests"}]},
            },
            {
                "type": "message",
                "id": "pi-call",
                "message": {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": THOUGHT},
                        {
                            "type": "toolCall",
                            "id": "call-1",
                            "name": "bash",
                            "arguments": {"command": f"pytest --token {TOOL_SECRET}"},
                        },
                    ],
                },
            },
            {
                "type": "message",
                "id": "pi-result",
                "message": {
                    "role": "toolResult",
                    "toolCallId": "call-1",
                    "toolName": "bash",
                    "content": [{"type": "text", "text": "3 failed " + "x" * 5000}],
                    "isError": False,
                },
            },
        ],
    )

    events = _scan(create_pi_adapter(_grant(PI_DESCRIPTOR.source_id, root, tmp_path)))

    call, result = _assert_tool_rows(events, name="bash")
    assert result.evidence.verbatim.startswith("[bash] 3 failed ")
    assert [item.evidence.message_id for item in (call, result)] == ["pi-call", "pi-result"]
    assert {item.evidence.project for item in (call, result)} == {"atlas"}


def test_qwen_code_imports_function_calls_and_responses_as_tool_evidence(
    tmp_path: Path,
) -> None:
    """Qwen Code records a tool result as `type: tool_result` wearing the user role.

    Shapes follow `chatRecordingService.ts`: `recordToolResult` writes
    `createUserContent(functionResponse parts)` with provenance `tool_result`.
    """
    root = tmp_path / "qwen-code"
    session = "a1b2c3d4-0000-4000-8000-000000000002"
    chats = root / "-Users-example-work-atlas" / "chats"
    chats.mkdir(parents=True)
    base = {"sessionId": session, "cwd": "/Users/example/work/atlas"}
    _write_jsonl(
        chats / f"{session}.jsonl",
        [
            {
                **base,
                "uuid": "q-call",
                "type": "assistant",
                "provenance": "assistant_output",
                "message": {
                    "role": "model",
                    "parts": [
                        {"text": THOUGHT, "thought": True},
                        {
                            "functionCall": {
                                "id": "call-1",
                                "name": "run_shell_command",
                                "args": {"command": f"pytest --token {TOOL_SECRET}"},
                            }
                        },
                    ],
                },
            },
            {
                **base,
                "uuid": "q-result",
                "type": "tool_result",
                "provenance": "tool_result",
                "message": {
                    "role": "user",
                    "parts": [
                        {
                            "functionResponse": {
                                "id": "call-1",
                                "name": "run_shell_command",
                                "response": {"output": "3 failed " + "x" * 5000},
                            }
                        }
                    ],
                },
            },
        ],
    )

    events = _scan(create_qwen_code_adapter(_grant(QWEN_CODE_DESCRIPTOR.source_id, root, tmp_path)))

    call, result = _assert_tool_rows(events, name="run_shell_command")
    assert result.evidence.verbatim.startswith("[run_shell_command] 3 failed ")
    assert [item.evidence.message_id for item in (call, result)] == ["q-call", "q-result"]
    assert not [item for item in _evidence(events) if item.evidence.written_by is WrittenBy.PERSON]


def test_deepseek_imports_tool_call_and_result_events_once(tmp_path: Path) -> None:
    """Shapes follow the harness's own snapshot `advanced/session.jsonl`.

    The call is stored twice upstream, as a `tool/call` event and as a
    `tool-call` block inside `assistant/message`; only the event is imported.
    A `tool/result` message wears the user role and is not the person.
    """
    root = tmp_path / "dsh"
    session = root / "run-t"
    session.mkdir(parents=True)
    arguments = json.dumps({"command": f"pytest --token {TOOL_SECRET}"})
    _write_jsonl(
        session / "session.jsonl",
        [
            {"type": "session", "id": "run-t", "cwd": "/private/project"},
            {
                "type": "assistant/message",
                "seq": 1,
                "data": {
                    "message": {
                        "id": "assistant-call",
                        "role": "assistant",
                        "content": [
                            {"type": "reasoning", "text": THOUGHT},
                            {
                                "type": "tool-call",
                                "id": "call-1",
                                "name": "shell",
                                "arguments": arguments,
                            },
                        ],
                    }
                },
            },
            {
                "type": "tool/call",
                "seq": 2,
                "data": {"callId": "call-1", "name": "shell", "arguments": arguments},
            },
            {
                "type": "tool/result",
                "seq": 3,
                "data": {
                    "message": {
                        "id": "result-1",
                        "role": "user",
                        "source": {"kind": "tool", "callId": "call-1"},
                        "content": [
                            {
                                "type": "tool-result",
                                "toolCallId": "call-1",
                                "content": [{"type": "text", "text": "3 failed " + "x" * 5000}],
                                "isError": False,
                            }
                        ],
                    }
                },
            },
        ],
    )

    events = _scan(
        create_deepseek_harness_adapter(
            _grant(DEEPSEEK_HARNESS_DESCRIPTOR.source_id, root, tmp_path)
        )
    )

    call, result = _assert_tool_rows(events, name="shell")
    assert len(_evidence(events)) == 2
    assert [item.evidence.message_id for item in (call, result)] == ["call-1", "result-1"]
    assert {item.evidence.project for item in (call, result)} == {"project"}


def test_pi_a_scan_stopped_between_a_reply_and_its_call_resumes_with_the_call(
    tmp_path: Path,
) -> None:
    """One record that says something and calls a tool is two rows.

    The first carries the cursor from before the record, so a scan that stops
    between them reads the record again instead of skipping past the call.
    """
    root = tmp_path / "pi"
    root.mkdir()
    call = {"type": "toolCall", "id": "k1", "name": "bash", "arguments": {"command": "ls"}}
    _write_jsonl(
        root / "session-r.jsonl",
        [
            {"type": "session", "id": "session-r", "cwd": "/private/project"},
            {"type": "message", "id": "u1", "message": {"role": "user", "content": "看看"}},
            {
                "type": "message",
                "id": "c1",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "好"}, call]},
            },
        ],
    )
    adapter = partial(create_pi_adapter, _grant(PI_DESCRIPTOR.source_id, root, tmp_path))

    rows = _evidence(_scan(adapter()))

    assert [(item.evidence.message_id, item.evidence.role) for item in rows] == [
        ("u1", "user"),
        ("c1", "assistant"),
        ("c1:tool", "tool"),
    ]
    stopped_after_text = {rows[1].cursor.file_key: rows[1].cursor}
    resumed = [
        item.evidence.message_id for item in _evidence(_scan(adapter(), cursors=stopped_after_text))
    ]
    assert "c1:tool" in resumed
    assert "u1" not in resumed


def test_hermes_a_scan_stopped_between_a_reply_and_its_call_resumes_with_the_call(
    tmp_path: Path,
) -> None:
    root = tmp_path / "hermes"
    root.mkdir()
    connection = sqlite3.connect(root / "state.db")
    connection.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, cwd TEXT);
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
            tool_calls TEXT, tool_name TEXT, timestamp INTEGER, active INTEGER
        );
        INSERT INTO sessions VALUES ('s', 'cli', '/private/project');
        """
    )
    calls = json.dumps([{"function": {"name": "terminal", "arguments": '{"command": "ls"}'}}])
    connection.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "s", "user", "看看", None, None, 1000, 1),
            (2, "s", "assistant", "好", calls, None, 1001, 1),
        ],
    )
    connection.commit()
    connection.close()
    adapter = partial(create_hermes_adapter, _grant(HERMES_DESCRIPTOR.source_id, root, tmp_path))

    rows = _evidence(_scan(adapter()))

    assert [(item.evidence.message_id, item.evidence.role) for item in rows] == [
        ("s:1", "user"),
        ("s:2", "assistant"),
        ("s:2:tool", "tool"),
    ]
    assert rows[2].evidence.verbatim.startswith("[terminal] ")
    stopped_after_text = {rows[1].cursor.file_key: rows[1].cursor}
    resumed = [
        item.evidence.message_id for item in _evidence(_scan(adapter(), cursors=stopped_after_text))
    ]
    assert "s:2:tool" in resumed
    assert "s:1" not in resumed


def test_hermes_imports_tool_rows_with_their_tool_name(tmp_path: Path) -> None:
    """`messages.tool_name` and `role = 'tool'` are Hermes's own columns.

    The column list is read off `hermes_state.py` (`_CONVERSATION_ROW_COLUMNS`).
    Reasoning has its own column and is not read.
    """
    root = tmp_path / "hermes"
    root.mkdir()
    connection = sqlite3.connect(root / "state.db")
    connection.execute(
        """
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
            tool_call_id TEXT, tool_calls TEXT, tool_name TEXT, reasoning TEXT,
            timestamp INTEGER, active INTEGER
        )
        """
    )
    connection.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (1, "s", "user", "run tests", None, None, None, None, 1000, 1),
            (2, "s", "assistant", "", None, None, None, THOUGHT, 1001, 1),
            (
                3,
                "s",
                "tool",
                f"pytest --token {TOOL_SECRET}",
                "c1",
                None,
                "terminal",
                None,
                1002,
                1,
            ),
            (4, "s", "tool", "3 failed " + "x" * 5000, "c1", None, "terminal", None, 1003, 1),
        ],
    )
    connection.commit()
    connection.close()
    (root / "sessions").mkdir()
    _write_jsonl(
        root / "sessions" / "rollout.jsonl",
        [{"role": "tool", "id": "r1", "tool_name": "browser", "content": "page loaded"}],
    )
    adapter = partial(create_hermes_adapter, _grant(HERMES_DESCRIPTOR.source_id, root, tmp_path))

    events = _scan(adapter())

    state_rows = [item for item in _evidence(events) if item.evidence.message_id != "r1"]
    call, result = _assert_tool_rows(
        [item for item in state_rows if item.evidence.role == "tool"], name="terminal"
    )
    assert [item.evidence.message_id for item in (call, result)] == ["s:3", "s:4"]
    assert THOUGHT not in repr(events)
    rollout = next(item for item in _evidence(events) if item.evidence.message_id == "r1")
    assert (rollout.evidence.verbatim, rollout.evidence.written_by) == (
        "[browser] page loaded",
        WrittenBy.TOOL,
    )
    assert not _evidence(_scan(adapter(), cursors=_cursors(events)))


def test_opencode_imports_tool_parts_and_resumes_inside_a_message(tmp_path: Path) -> None:
    """OpenCode's `ToolPart`: `{type: "tool", tool, callID, state: {input, output}}`.

    One message holds text, reasoning and tool parts. Each tool part becomes its
    own row, so a scan stopped between two rows of one message must resume
    without losing the rest of it.
    """
    root = tmp_path / "opencode"
    root.mkdir()
    connection = sqlite3.connect(root / "opencode.db")
    connection.executescript(
        """
        CREATE TABLE session (id TEXT PRIMARY KEY, directory TEXT);
        CREATE TABLE message (
            id TEXT PRIMARY KEY, session_id TEXT, time_created INTEGER, data TEXT
        );
        CREATE TABLE part (
            id TEXT PRIMARY KEY, message_id TEXT, time_created INTEGER, data TEXT
        );
        INSERT INTO session VALUES ('s', '/private/project');
        INSERT INTO message VALUES ('m1', 's', 1000, '{"role":"assistant"}');
        INSERT INTO message VALUES ('m2', 's', 2000, '{"role":"assistant"}');
        """
    )

    def part(part_id: str, message_id: str, at: int, data: dict[str, object]) -> None:
        connection.execute(
            "INSERT INTO part VALUES (?, ?, ?, ?)", (part_id, message_id, at, json.dumps(data))
        )

    part("p-reason", "m1", 1000, {"type": "reasoning", "text": THOUGHT})
    part("p-text", "m1", 1001, {"type": "text", "text": "running the tests"})
    part(
        "p-call",
        "m1",
        1002,
        {
            "type": "tool",
            "tool": "bash",
            "callID": "c1",
            "state": {
                "status": "running",
                "input": {"command": f"pytest --token {TOOL_SECRET}"},
            },
        },
    )
    part(
        "p-result",
        "m2",
        2000,
        {
            "type": "tool",
            "tool": "bash",
            "callID": "c2",
            "state": {
                "status": "completed",
                "input": {"command": "pytest"},
                "output": "3 failed " + "x" * 5000,
            },
        },
    )
    connection.commit()
    connection.close()
    adapter = partial(
        create_opencode_adapter, _grant(OPENCODE_DESCRIPTOR.source_id, root, tmp_path)
    )

    events = _scan(adapter())

    assert [item.evidence.message_id for item in _evidence(events)] == ["m1", "p-call", "p-result"]
    assert _evidence(events)[0].evidence.verbatim == "running the tests"
    _assert_tool_rows(events, name="bash")
    assert not _evidence(_scan(adapter(), cursors=_cursors(events)))
    stopped_after_text = {
        _evidence(events)[0].cursor.file_key: _evidence(events)[0].cursor,
    }
    resumed = [
        item.evidence.message_id for item in _evidence(_scan(adapter(), cursors=stopped_after_text))
    ]
    assert "p-call" in resumed and "p-result" in resumed


def test_openclaw_tool_reader_cannot_bypass_the_paused_factory(tmp_path: Path) -> None:
    """OpenClaw's reader shape is retained, but product pause blocks the entry."""
    root = tmp_path / "openclaw"
    root.mkdir()
    connection = sqlite3.connect(root / "conversations.db")
    connection.executescript(
        """
        CREATE TABLE conversations (id TEXT PRIMARY KEY, workspace_path TEXT, git_root TEXT);
        CREATE TABLE messages (
            id TEXT PRIMARY KEY, conversation_id TEXT, role TEXT, content TEXT, created_at TEXT
        );
        INSERT INTO conversations VALUES ('c', '/private/project', NULL);
        """
    )
    connection.executemany(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
        [
            ("u", "c", "user", "run tests", "2026-09-01"),
            ("t1", "c", "tool", f"pytest --token {TOOL_SECRET}", "2026-09-01"),
            ("t2", "c", "tool", "3 failed " + "x" * 5000, "2026-09-01"),
        ],
    )
    connection.commit()
    connection.close()
    with pytest.raises(SourceAccessError) as refused:
        create_openclaw_adapter(_grant(OPENCLAW_DESCRIPTOR.source_id, root, tmp_path))
    assert refused.value.code is SourceAccessErrorCode.GRANT_REQUIRED


def _grant(source_id: str, root: Path, home: Path) -> AuthorizedSourceRoot:
    return AuthorizedSourceRoot.grant(
        source_id=source_id,
        root=root,
        user_confirmed=True,
        home=home,
    )


def _scan(
    adapter: SourceAdapter,
    *,
    cursors: dict[str, SourceCursor] | None = None,
) -> list[ScanEvent]:
    async def collect() -> list[ScanEvent]:
        return [event async for event in adapter.scan(ScanOptions(cursors=cursors or {}))]

    return asyncio.run(collect())


def _evidence(events: list[ScanEvent]) -> list[ScannedEvidence]:
    return [event for event in events if isinstance(event, ScannedEvidence)]


def _evidence_text(events: list[ScanEvent]) -> list[str]:
    return [event.evidence.verbatim for event in _evidence(events)]


def _skip_codes(events: list[ScanEvent]) -> set[ScanIssueCode]:
    """Every reason a record was not turned into evidence."""
    return {
        code
        for event in events
        for code in (
            [getattr(event, "reason_code", None)]
            + [issue.code for issue in getattr(event, "issues", ())]
        )
        if code is not None
    }


def _cursors(events: list[ScanEvent]) -> dict[str, SourceCursor]:
    return {
        event.cursor.file_key: event.cursor
        for event in events
        if getattr(event, "cursor", None) is not None
    }


def _write_jsonl(path: Path, records: list[dict[str, object]]) -> None:
    path.write_text(
        "".join(f"{json.dumps(record)}\n" for record in records),
        encoding="utf-8",
    )
