"""Core's intake for host hooks: visible text only, staged under Core's writer.

The hook process (`capture_handoff`) never opens the database; it posts the
hook's JSON to `POST /api/v1/live-capture/{source}` on the running Core, which
calls `capture_hook` here. Tool output stays out.

A source that live sync already reads from its own files is left to that
path: the hook and the file name the same message differently (the hook has
no workspace and often no message id), so staging both sent one turn to the
SDK twice. Only a source no file poller follows is staged from the hook.
"""

import asyncio
import json
import time
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.params import Depends
from pydantic import JsonValue

from one_memory.capture_handoff import (
    CAPTURE_ROUTE,
    CAPTURE_SOURCES,
    MAX_PAYLOAD_BYTES,
    SDK_MIGRATION_CAPTURE_FENCE,
    sdk_migration_capture_fenced,
)
from one_memory.database_access import main_database
from one_memory.derived_echo import PREFACE, strip_injected_memory
from one_memory.imports.access import opaque_workspace_id, safe_project_label
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    EvidenceRecord,
    OriginMetadata,
    WrittenBy,
    materialize_write_input,
)
from one_memory.imports.sources.claude_code import parse_claude_code_record
from one_memory.imports.sources.codex import HARNESS_PREFIXES, parse_codex_record
from one_memory.imports.sources.common import (
    ParsedMessage,
    SourceRecordContext,
    row_ordinal,
    sortable_ordinal,
)
from one_memory.imports.sources.deepseek_harness import parse_deepseek_harness_record
from one_memory.imports.sources.parsing import (
    as_record,
    first_string,
    is_host_generated_user_turn,
    text_content,
    unwrap_host_envelope,
)
from one_memory.imports.sources.pi import _is_automated_session
from one_memory.imports.sources.workbuddy import _is_scheduled_automation, parse_workbuddy_record
from one_memory.memory_server.durable_queue import DurableWriteQueue
from one_memory.sdk_data_plane_migration import MemoryAuthority

__all__ = [
    "CAPTURE_SOURCES",
    "SDK_MIGRATION_CAPTURE_FENCE",
    "CaptureOutcome",
    "CaptureRejection",
    "capture_hook",
    "capture_rejection",
    "live_capture_router",
    "record_capture_rejection",
    "sdk_migration_capture_fenced",
]

_UNKNOWN_USER = frozenset({"hermes", "opencode", "openclaw"})
_USER_FIELDS = {
    "UserPromptSubmit": "prompt",
    "beforeSubmitPrompt": "prompt",
    "before_agent_start": "prompt",
}
_ASSISTANT_FIELDS = {
    "Stop": "last_assistant_message",
    "stop": "last_assistant_message",
    "SubagentStop": "last_assistant_message",
    "StopFailure": "last_assistant_message",
    "afterAgentResponse": "text",
}
_FILE_FALLBACK = {
    "workbuddy": parse_workbuddy_record,
    "claude-code": parse_claude_code_record,
    "codex": parse_codex_record,
}
_FALLBACK_EVENTS = frozenset({"Stop", "stop", "SubagentStop", "StopFailure"})
_TEXT_BLOCKS = frozenset({"text"})


class CaptureOutcome(StrEnum):
    """What Core did with one hook payload. Never the payload itself."""

    STAGED = "staged"
    #: Live sync reads this source's own files; that path owns the turn.
    FILE_POLLED = "file_polled"
    REJECTED = "rejected"
    IGNORED = "ignored"


def capture_hook(
    source_id: str,
    payload: object,
    database: Path,
    *,
    queue: DurableWriteQueue,
    file_polled: Callable[[str], bool] = lambda _source_id: False,
) -> CaptureOutcome:
    """Gate one hook payload and stage its visible lines, inside Core.

    Runs on SDK authority through Core's single writer: the lines become
    write-job input exactly as live file sync stages them
    (`materialize_write_input` -> `stage_sdk_input` -> `finalize_sdk_file`,
    live priority). No Evidence table is involved.
    """
    if source_id not in CAPTURE_SOURCES or not isinstance(payload, dict):
        return CaptureOutcome.IGNORED
    # Cursor also runs ~/.claude/settings.json hooks; its own hook already files the turn.
    if source_id != "cursor" and "cursor_version" in payload:
        return CaptureOutcome.IGNORED
    if not database.is_file():
        return CaptureOutcome.IGNORED
    rejection = capture_rejection(database, source_id)
    if rejection is not None:
        # Silent, and the reason is counted rather than printed: a hook that
        # spoke would put this into the host's turn as text.
        record_capture_rejection(database, source_id, rejection)
        return CaptureOutcome.REJECTED
    if source_id == "pi" and _is_automated_session(first_string(payload.get("cwd"))):
        return CaptureOutcome.IGNORED
    if file_polled(source_id):
        # The same turn reaches the SDK once, from the file: the two lanes give
        # one message different identities, so staging both wrote it twice.
        return CaptureOutcome.FILE_POLLED
    inputs = _hook_inputs(source_id, payload) or _capture_transcript(source_id, payload, database)
    if not inputs:
        return CaptureOutcome.IGNORED
    observed = datetime.now(UTC)
    conversations: dict[str, list[EvidenceRecord]] = {}
    for value in inputs:
        conversations.setdefault(value.conversation_id, []).append(
            materialize_write_input(value, observed_at=observed)
        )
    for conversation_id, records in conversations.items():
        # The turn rule decides what is written (`turn_batches`): a turn is
        # complete when the next user message arrives, batches are 5 turns or
        # 8000 characters, and Core writes the rest after 10 quiet minutes.
        queue.assemble_live_records(tuple(records), file_key=f"hook:{conversation_id}")
    return CaptureOutcome.STAGED


def _hook_inputs(source_id: str, payload: dict[str, object]) -> list[EvidenceInput]:
    conversation_id = _conversation(payload)
    if conversation_id is None:
        return []
    observed = datetime.now(UTC)
    return [
        EvidenceInput(
            source_id=source_id,
            conversation_id=conversation_id,
            message_id=message_id,
            source_ordinal=None if message_id else "hook-visible",
            role=role,
            written_by=written_by,
            created_at=observed,
            verbatim=verbatim,
            origin_meta={EvidenceOrigin.HOOK: OriginMetadata()},
        )
        for role, written_by, verbatim, message_id in _messages(source_id, payload)
    ]


def live_capture_router(
    dependencies: list[Depends],
    *,
    file_polled: Callable[[Request, str], bool],
) -> APIRouter:
    """`POST /api/v1/live-capture/{source_id}`: a hook's payload, handed over by the hook.

    202 whatever the gates decide, so a hook has nothing to print. The answer
    names the outcome only, never any of the payload.
    """
    router = APIRouter(dependencies=dependencies)

    @router.post(CAPTURE_ROUTE, status_code=202)
    async def live_capture(source_id: str, request: Request) -> dict[str, str]:
        if source_id not in CAPTURE_SOURCES:
            raise HTTPException(status_code=404, detail="capture_source_not_found")
        body = await request.body()
        if len(body) > MAX_PAYLOAD_BYTES:
            raise HTTPException(status_code=413, detail="capture_payload_too_large")
        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeError) as error:
            raise HTTPException(status_code=422, detail="capture_payload_invalid") from error
        state = request.app.state
        queue = getattr(state, "memory_write_queue", None)
        if getattr(state, "memory_authority", None) is not MemoryAuthority.SDK or queue is None:
            # Only an SDK-authority home is served; nothing here may bring the
            # retired Evidence tables back for any other.
            return {"outcome": CaptureOutcome.IGNORED.value}
        outcome = await asyncio.to_thread(
            capture_hook,
            source_id,
            payload,
            queue.database_path,
            queue=queue,
            file_polled=lambda source: file_polled(request, source),
        )
        return {"outcome": outcome.value}

    return router


class CaptureRejection(StrEnum):
    """Why a hook's turn was dropped, or nothing at all.

    Capture fails open on purpose: a hook that raises or prints would be read
    back into the host's turn, so a refusal has to be silent and exit 0. It was
    also completely invisible, which is why "持续同步开着但一条都没进" could not be
    told apart from "capture 坏了". These values name the reason without any
    payload, path, session or message — the only part that may be recorded.
    """

    SYNC_DISABLED = "sync_disabled"
    GRANT_MISSING = "grant_missing"
    GRANT_REVOKED = "grant_revoked"


def capture_rejection(database: Path, source_id: str) -> CaptureRejection | None:
    """None when this turn may be captured, otherwise why it may not."""
    from one_memory.memory_server.config_state import ConfigStore

    if not ConfigStore(database.parent).sync_enabled(source_id):
        return CaptureRejection.SYNC_DISABLED
    with main_database(database).read() as connection:
        row = connection.execute(
            "SELECT revoked_at FROM source_grants WHERE user_id='local' AND source_id=?",
            (source_id,),
        ).fetchone()
    if row is None:
        return CaptureRejection.GRANT_MISSING
    # Indexed, not named: this connection has no row factory, and a hook is
    # not the place to add one.
    if row[0] is not None:
        return CaptureRejection.GRANT_REVOKED
    return None


def record_capture_rejection(
    database: Path,
    source_id: str,
    reason: CaptureRejection,
    *,
    window_seconds: float = 600.0,
) -> None:
    """Note one dropped turn, at most once per window per source and reason.

    Best effort by construction: a hook runs inside somebody's turn, so a sink
    that is slow, full or read-only must never surface here. The record is a
    count and nothing else — no payload, no path, no session or conversation
    id — because this file is meant to be handed to support as is.

    One small file per source and reason, rather than a line per turn: capture
    runs on every prompt, and a log that grew with the conversation would be a
    privacy problem wearing a diagnosis as a disguise.
    """
    try:
        directory = database.parent / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        marker = directory / f"capture-rejection-{source_id}-{reason.value}.json"
        now = time.time()
        payload: dict[str, object] = {"source_id": source_id, "reason": reason.value}
        if marker.exists() and now - marker.stat().st_mtime < window_seconds:
            payload = json.loads(marker.read_text(encoding="utf8"))
            previous = payload.get("count", 1)
            payload["count"] = int(previous) + 1 if isinstance(previous, (str, int, float)) else 2
        else:
            payload["count"] = 1
            payload["window_started_at"] = datetime.fromtimestamp(now, UTC).isoformat()
            payload["last_at"] = payload["window_started_at"]
        marker.write_text(json.dumps(payload, sort_keys=True), encoding="utf8")
    except Exception:
        return


def _conversation(payload: dict[str, object]) -> str | None:
    return first_string(
        cast(JsonValue | None, payload.get("session_id")),
        cast(JsonValue | None, payload.get("sessionId")),
        cast(JsonValue | None, payload.get("conversation_id")),
        cast(JsonValue | None, payload.get("conversationId")),
    )


def _hook_id(payload: dict[str, object]) -> str | None:
    return first_string(
        cast(JsonValue | None, payload.get("message_id")),
        cast(JsonValue | None, payload.get("messageId")),
        cast(JsonValue | None, payload.get("uuid")),
        cast(JsonValue | None, payload.get("prompt_id")),
    )


def _messages(
    source_id: str, payload: dict[str, object]
) -> list[tuple[str, WrittenBy, str, str | None]]:
    """Visible lines only. The last item is a conversation id when the line has its own."""

    event = payload.get("hook_event_name") or payload.get("hookEventName")
    if not isinstance(event, str):
        return []
    message_id = _hook_id(payload)
    if event in _USER_FIELDS:
        raw = payload.get(_USER_FIELDS[event])
        if not isinstance(raw, str):
            return []
        text, scheduled = _user_text(source_id, raw)
        if text is None:
            return []
        author = WrittenBy.UNKNOWN if scheduled or source_id in _UNKNOWN_USER else WrittenBy.PERSON
        return [("user", author, text, message_id)]
    if event in _ASSISTANT_FIELDS:
        raw = payload.get(_ASSISTANT_FIELDS[event])
        if not isinstance(raw, str):
            return []
        text = _plain(raw)
        if text is None:
            return []
        return [("assistant", WrittenBy.AGENT, text, message_id)]
    if event in {"turn_end", "agent_settled"}:
        return _role_message(payload.get("message"), message_id, source_id)
    if event == "sync_turn":
        # One shared id would collapse the user line and the reply into a single row.
        return [
            *_user_line(source_id, payload.get("user"), None),
            *_assistant_line(payload.get("assistant"), None),
        ]
    if event == "agent_end":
        return _role_list(payload.get("messages"), source_id)
    if event == "turn/end":
        return _deepseek_records(payload.get("records"))
    if event in {"session.status", "session.idle"}:
        if event == "session.status" and payload.get("status") not in {None, "idle"}:
            return []
        return _opencode_messages(payload.get("messages"))
    return []


def _user_text(source_id: str, raw: str) -> tuple[str | None, bool]:
    envelope = unwrap_host_envelope(strip_injected_memory(raw))
    scheduled = source_id == "workbuddy" and any(
        _is_scheduled_automation(block) for block in envelope.prefix_blocks
    )
    text = _plain(envelope.text or "")
    if text is None or is_host_generated_user_turn(text):
        return None, scheduled
    if source_id == "codex" and text.lstrip().casefold().startswith(HARNESS_PREFIXES):
        return None, scheduled
    return text, scheduled


def _plain(value: str) -> str | None:
    kept = [
        line
        for line in value.splitlines()
        if line.strip() and line.strip() != PREFACE and "om-derived:" not in line
    ]
    text = "\n".join(kept).strip()
    return text or None


def _user_line(
    source_id: str, raw: object, message_id: str | None
) -> list[tuple[str, WrittenBy, str, str | None]]:
    if not isinstance(raw, str):
        return []
    text, scheduled = _user_text(source_id, raw)
    if text is None:
        return []
    author = WrittenBy.UNKNOWN if scheduled or source_id in _UNKNOWN_USER else WrittenBy.PERSON
    return [("user", author, text, message_id)]


def _assistant_line(
    raw: object, message_id: str | None
) -> list[tuple[str, WrittenBy, str, str | None]]:
    if not isinstance(raw, str):
        return []
    text = _plain(raw)
    if text is None:
        return []
    return [("assistant", WrittenBy.AGENT, text, message_id)]


def _role_message(
    value: object, message_id: str | None, source_id: str
) -> list[tuple[str, WrittenBy, str, str | None]]:
    record = as_record(value if isinstance(value, dict) else None)
    if record is None:
        return _assistant_line(value, message_id)
    role = first_string(record.get("role")) or "assistant"
    if role in {"tool", "toolResult", "system"}:
        return []
    text = text_content(record.get("content"), allowed_block_types=_TEXT_BLOCKS)
    if text is None:
        text = _plain(first_string(record.get("text")) or "")
    if text is None:
        return []
    own_id = first_string(record.get("id"), record.get("messageId")) or message_id
    if role == "user":
        return _user_line(source_id, text, own_id)
    if role != "assistant":
        return []
    return [("assistant", WrittenBy.AGENT, text, own_id)]


def _role_list(value: object, source_id: str) -> list[tuple[str, WrittenBy, str, str | None]]:
    if not isinstance(value, list):
        return []
    lines: list[tuple[str, WrittenBy, str, str | None]] = []
    for item in value:
        lines.extend(_role_message(item, None, source_id))
    return lines


def _deepseek_records(
    value: object,
) -> list[tuple[str, WrittenBy, str, str | None]]:
    if not isinstance(value, list):
        return []
    lines: list[tuple[str, WrittenBy, str, str | None]] = []
    for index, item in enumerate(value):
        record = as_record(item if isinstance(item, dict) else None)
        if record is None:
            continue
        parsed = parse_deepseek_harness_record(
            record,
            SourceRecordContext(
                file_key="hook", relative_path="session/session.jsonl", ordinal=str(index)
            ),
        )
        if isinstance(parsed, ParsedMessage):
            lines.append((parsed.role, parsed.written_by, parsed.verbatim, parsed.message_id))
    return lines


def _opencode_messages(
    value: object,
) -> list[tuple[str, WrittenBy, str, str | None]]:
    if not isinstance(value, list):
        return []
    lines: list[tuple[str, WrittenBy, str, str | None]] = []
    for item in value:
        record = as_record(item if isinstance(item, dict) else None)
        if record is None:
            continue
        role = first_string(record.get("role"))
        if role not in {"user", "assistant"}:
            continue
        parts = record.get("parts")
        texts: list[str] = []
        if isinstance(parts, list):
            for part in parts:
                part_record = as_record(part if isinstance(part, dict) else None)
                if part_record is None or part_record.get("type") != "text":
                    continue
                text = first_string(part_record.get("text"))
                if text is not None:
                    texts.append(text)
        verbatim = _plain("\n".join(texts))
        if verbatim is None:
            continue
        author = WrittenBy.UNKNOWN if role == "user" else WrittenBy.AGENT
        lines.append(
            (
                role,
                author,
                verbatim,
                first_string(record.get("message_id"), record.get("id")),
            )
        )
    return lines


def _capture_transcript(
    source_id: str, payload: dict[str, object], database: Path
) -> list[EvidenceInput]:
    """Stop without in-memory text reads the granted transcript, then the same parser."""

    event = payload.get("hook_event_name") or payload.get("hookEventName")
    parser = _FILE_FALLBACK.get(source_id)
    transcript = payload.get("transcript_path")
    if event not in _FALLBACK_EVENTS or parser is None or not isinstance(transcript, str):
        return []
    root = _grant_root(database, source_id)
    if root is None:
        return []
    try:
        granted = root.resolve()
        target = Path(transcript).resolve()
    except OSError:
        return []
    if target != granted and granted not in target.parents:
        return []
    if not target.is_file():
        return []
    _wait_stable(target)
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError:
        return []
    relative = target.relative_to(granted).as_posix()
    inputs: list[EvidenceInput] = []
    for index, line in enumerate(raw.splitlines()):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        parsed = parser(
            record,
            SourceRecordContext(file_key="hook", relative_path=relative, ordinal=str(index)),
        )
        if not isinstance(parsed, ParsedMessage):
            continue
        for row_index, row in enumerate(parsed.rows()):
            inputs.append(
                EvidenceInput(
                    source_id=source_id,
                    workspace=opaque_workspace_id(source_id, row.workspace),
                    project=row.project or safe_project_label(row.workspace),
                    conversation_id=row.conversation_id,
                    message_id=row.message_id,
                    source_ordinal=(
                        sortable_ordinal(row_ordinal(f"file:{index}", row_index))
                        if row.message_id
                        else row_ordinal(f"file-{index}", row_index)
                    ),
                    role=row.role,
                    written_by=row.written_by,
                    created_at=row.created_at,
                    verbatim=row.verbatim,
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            )
    return inputs


def _grant_root(database: Path, source_id: str) -> Path | None:
    with main_database(database).read() as connection:
        row = connection.execute(
            """
            SELECT root_path FROM source_grants
            WHERE user_id = 'local' AND source_id = ? AND revoked_at IS NULL
            """,
            (source_id,),
        ).fetchone()
    if row is None or not isinstance(row[0], str):
        return None
    return Path(row[0])


def _wait_stable(path: Path, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    previous = -1
    while True:
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size == previous:
            return
        previous = size
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        time.sleep(min(0.05, remaining))
