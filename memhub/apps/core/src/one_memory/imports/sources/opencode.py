import hashlib
import json
import sqlite3
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import datetime
from functools import partial

from pydantic import JsonValue

from one_memory.imports.access import AuthorizedSourceFile, AuthorizedSourceRoot
from one_memory.imports.adapters import (
    ScanEvent,
    ScanFailure,
    ScanIssueCode,
    ScannedEvidence,
    ScanOptions,
    ScanPhase,
    ScanProgressEvent,
    ScanSkip,
    SourceCapabilities,
    SourceCursor,
    SourceDescriptor,
    StorageKind,
)
from one_memory.imports.async_bridge import iterate_in_worker
from one_memory.imports.discovery import (
    SourceFileDiscoveryFailure,
    discovered_off_loop,
)
from one_memory.imports.models import WrittenBy
from one_memory.imports.sources.common import (
    ParsedMessage,
    discovery_failure_code,
    resume_checkpoint,
    source_cursor,
    to_evidence,
)
from one_memory.imports.sources.parsing import parse_timestamp, tool_evidence_text
from one_memory.imports.sqlite_access import open_sqlite_readonly

OPENCODE_DESCRIPTOR = SourceDescriptor(
    source_id="opencode",
    display_name="OpenCode",
    storage_kinds=frozenset({StorageKind.SQLITE}),
    capabilities=SourceCapabilities(
        can_append=True,
        can_observe_edits=False,
        can_observe_deletes=False,
        can_reconcile_complete_set=False,
    ),
)


class OpenCodeSourceAdapter:
    def __init__(
        self,
        descriptor: SourceDescriptor,
        grant: AuthorizedSourceRoot,
        *,
        source_version: str | None = None,
    ) -> None:
        self._descriptor = descriptor
        self.grant = grant
        self.source_version = source_version

    @property
    def descriptor(self) -> SourceDescriptor:
        return self._descriptor

    async def detect(self) -> bool:
        async for item in discovered_off_loop(
            self.grant,
            suffixes=frozenset({".db"}),
            max_targets=10_000,
        ):
            if isinstance(item, AuthorizedSourceFile) and item.relative_path.name == "opencode.db":
                return True
        return False

    async def scan(self, options: ScanOptions) -> AsyncIterator[ScanEvent]:
        emitted = 0
        async for discovered in discovered_off_loop(
            self.grant,
            suffixes=frozenset({".db"}),
            max_targets=options.max_scan_targets or 100_000,
        ):
            yield ScanProgressEvent(
                phase=ScanPhase.DISCOVER,
                discovered_increment=1,
            )
            if isinstance(discovered, SourceFileDiscoveryFailure):
                yield ScanFailure(reason_code=discovery_failure_code(discovered))
                continue
            if discovered.relative_path.name != "opencode.db":
                continue
            try:
                async for event in iterate_in_worker(
                    partial(
                        _read_opencode_db,
                        discovered,
                        options,
                        self.descriptor.source_id,
                        self.source_version,
                    ),
                    scope_key=self.descriptor.source_id,
                ):
                    yield event
                    if isinstance(event, ScannedEvidence):
                        emitted += 1
                        if options.max_messages is not None and emitted >= options.max_messages:
                            return
            except sqlite3.Error:
                yield ScanFailure(reason_code=ScanIssueCode.SOURCE_BUSY)
        yield ScanProgressEvent(phase=ScanPhase.DONE)


def create_opencode_adapter(
    grant: AuthorizedSourceRoot,
    *,
    source_version: str | None = None,
) -> OpenCodeSourceAdapter:
    return OpenCodeSourceAdapter(
        descriptor=OPENCODE_DESCRIPTOR,
        grant=grant,
        source_version=source_version,
    )


#: An assistant message is written once and then updated in place while it
#: streams. Reading it before `time.completed` exists would persist a truncated
#: reply and a cursor that never looks back. A message that has stayed
#: unfinished this long was interrupted (or the app died), so its visible text
#: is all the person ever saw.
_OPENCODE_SETTLE_MS = 10 * 60 * 1000

_V1_TABLES: tuple[tuple[str, set[str]], ...] = (
    ("message", {"id", "session_id", "time_created", "data"}),
    ("part", {"id", "message_id", "time_created", "data"}),
    ("session", {"id", "directory"}),
)
_V2_MESSAGE_COLUMNS = {
    "id",
    "session_id",
    "type",
    "time_created",
    "time_updated",
    "data",
}


def _read_opencode_db(
    source_file: AuthorizedSourceFile,
    options: ScanOptions,
    source_id: str,
    source_version: str | None,
) -> Iterator[ScanEvent]:
    checkpoint = resume_checkpoint(source_file, options)
    last_rowid = _row_checkpoint(checkpoint)
    previous = options.cursors.get(source_file.safe_id)
    anchor = previous.checkpoint_anchor if previous is not None else None
    with open_sqlite_readonly(source_file) as connection:
        schema = _opencode_schema(connection)
        if schema is None:
            yield ScanFailure(reason_code=ScanIssueCode.UNSUPPORTED_SCHEMA)
            return
        last_rowid = _validated_row_checkpoint(
            connection,
            table="session_message" if schema == "v2" else "message",
            last_rowid=last_rowid,
            checkpoint_anchor=anchor,
        )
        reader = _read_v2_rows if schema == "v2" else _read_v1_rows
        yield from reader(
            connection,
            source_file=source_file,
            source_id=source_id,
            source_version=source_version,
            last_rowid=last_rowid,
            checkpoint_anchor=anchor if last_rowid else None,
        )


def _opencode_schema(connection: sqlite3.Connection) -> str | None:
    """Which session store this database has, read from its tables alone.

    OpenCode 2 rewrote the store (`session_message`/`session_v2`) without
    changing the file name, so the database on disk decides, not the app
    version. A future rename of the session table is still accepted as long as
    it keeps `id` and `directory`.
    """
    if _has_columns(connection, "session_message", _V2_MESSAGE_COLUMNS) and _v2_session_table(
        connection
    ):
        return "v2"
    if all(_has_columns(connection, table, columns) for table, columns in _V1_TABLES):
        return "v1"
    return None


def _v2_session_table(connection: sqlite3.Connection) -> str | None:
    for table in ("session_v2", "session"):
        if _has_columns(connection, table, {"id", "directory"}):
            return table
    return None


def _read_v1_rows(
    connection: sqlite3.Connection,
    *,
    source_file: AuthorizedSourceFile,
    source_id: str,
    source_version: str | None,
    last_rowid: int,
    checkpoint_anchor: str | None,
) -> Iterator[ScanEvent]:
    rows = connection.execute(
        """
        SELECT m.rowid AS source_rowid, m.id AS message_id,
               m.session_id, m.time_created,
               m.data AS message_data, s.directory,
               (
                   SELECT json_group_array(
                       json_object('id', ordered_part.id, 'data', json(ordered_part.data))
                   )
                   FROM (
                       SELECT p.id, p.data
                       FROM part p
                       WHERE p.message_id = m.id
                         AND json_valid(p.data)
                         AND json_extract(p.data, '$.type') IN ('text', 'tool')
                       ORDER BY p.time_created, p.rowid
                   ) AS ordered_part
               ) AS parts
        FROM message m
        LEFT JOIN session s ON s.id = m.session_id
        WHERE m.rowid > ?
        ORDER BY m.rowid
        """,
        (last_rowid,),
    )
    # A message can yield several rows. All but its last carry the cursor
    # from before the message, so a scan stopped between them re-reads it;
    # the part and message ids keep the re-read from duplicating anything.
    before = source_cursor(
        source_file,
        f"row:{last_rowid}",
        checkpoint_anchor=checkpoint_anchor,
    )
    for row in rows:
        message_id = str(row["message_id"])
        source_rowid = int(row["source_rowid"])
        cursor = source_cursor(
            source_file,
            f"row:{source_rowid}",
            checkpoint_anchor=_checkpoint_anchor(message_id),
        )
        message_data = _json_object(row["message_data"])
        role = message_data.get("role") if message_data else None
        conversation_id = str(row["session_id"])
        if role not in {"user", "assistant"}:
            yield ScanSkip(
                cursor=cursor,
                conversation_id=conversation_id,
                reason_code=ScanIssueCode.TOOL_CONTENT_EXCLUDED,
            )
            before = cursor
            continue
        yield from _emit_message(
            source_file=source_file,
            source_id=source_id,
            source_version=source_version,
            source_rowid=source_rowid,
            message_id=message_id,
            conversation_id=conversation_id,
            role=str(role),
            workspace=row["directory"] if isinstance(row["directory"], str) else None,
            created_at=parse_timestamp(_timestamp_value(row["time_created"])),
            parts=_message_parts(row["parts"]),
            cursor=cursor,
            before=before,
            advance=True,
        )
        before = cursor


def _read_v2_rows(
    connection: sqlite3.Connection,
    *,
    source_file: AuthorizedSourceFile,
    source_id: str,
    source_version: str | None,
    last_rowid: int,
    checkpoint_anchor: str | None,
) -> Iterator[ScanEvent]:
    session_table = _v2_session_table(connection)
    if session_table is None:  # pragma: no cover - guarded by _opencode_schema
        yield ScanFailure(reason_code=ScanIssueCode.UNSUPPORTED_SCHEMA)
        return
    rows = connection.execute(
        f"""
        SELECT m.rowid AS source_rowid, m.id AS message_id, m.session_id,
               m.time_created, m.time_updated, m.type AS message_type,
               m.data AS message_data, s.directory
        FROM session_message m
        LEFT JOIN "{session_table}" s ON s.id = m.session_id
        WHERE m.rowid > ?
        ORDER BY m.rowid
        """,
        (last_rowid,),
    )
    before = source_cursor(
        source_file,
        f"row:{last_rowid}",
        checkpoint_anchor=checkpoint_anchor,
    )
    now_ms = time.time() * 1000
    # Once a message is still streaming, nothing after it may move the cursor
    # past it. Later rows are still reported, but under the earlier cursor, so
    # the next poll re-reads them and the unfinished message gets its turn.
    blocked = False
    for row in rows:
        message_id = str(row["message_id"])
        source_rowid = int(row["source_rowid"])
        cursor = source_cursor(
            source_file,
            f"row:{source_rowid}",
            checkpoint_anchor=_checkpoint_anchor(message_id),
        )
        message_type = str(row["message_type"])
        conversation_id = str(row["session_id"])
        if message_type not in {"user", "assistant"}:
            # Idle/usage/compaction rows carry no words; they still move the
            # cursor when nothing earlier is waiting to settle.
            if not blocked:
                before = cursor
            continue
        message_data = _json_object(row["message_data"])
        if message_type == "assistant" and not _assistant_settled(
            message_data, row["time_updated"], now_ms
        ):
            blocked = True
            continue
        yield from _emit_message(
            source_file=source_file,
            source_id=source_id,
            source_version=source_version,
            source_rowid=source_rowid,
            message_id=message_id,
            conversation_id=conversation_id,
            role=message_type,
            workspace=row["directory"] if isinstance(row["directory"], str) else None,
            created_at=parse_timestamp(_timestamp_value(row["time_created"])),
            parts=_v2_message_parts(message_data, message_type, message_id),
            cursor=cursor,
            before=before,
            advance=not blocked,
        )
        if not blocked:
            before = cursor


def _emit_message(
    *,
    source_file: AuthorizedSourceFile,
    source_id: str,
    source_version: str | None,
    source_rowid: int,
    message_id: str,
    conversation_id: str,
    role: str,
    workspace: str | None,
    created_at: datetime | None,
    parts: list[tuple[str, bool, str]],
    cursor: SourceCursor,
    before: SourceCursor,
    advance: bool,
) -> Iterator[ScanEvent]:
    # One message spans several steps: text, a tool call, more text. Rows
    # follow the parts in that order; each run of text is one row, the first
    # under the message's own id.
    parsed_rows: list[ParsedMessage] = []
    for part_id, is_tool, text in parts:
        if not is_tool and parsed_rows and parsed_rows[-1].role != "tool":
            last_text = parsed_rows[-1]
            parsed_rows[-1] = replace(last_text, verbatim=f"{last_text.verbatim}\n{text}")
            continue
        first_text = not is_tool and not any(r.role != "tool" for r in parsed_rows)
        parsed_rows.append(
            ParsedMessage(
                conversation_id=conversation_id,
                message_id=message_id if first_text else part_id,
                role="tool" if is_tool else role,
                written_by=(
                    WrittenBy.TOOL
                    if is_tool
                    else WrittenBy.UNKNOWN
                    if role == "user"
                    else WrittenBy.AGENT
                ),
                created_at=created_at,
                verbatim=text,
                workspace=workspace,
                record_type="tool_part" if is_tool else "message_parts",
            )
        )
    if not parsed_rows:
        yield ScanSkip(
            cursor=cursor if advance else before,
            conversation_id=conversation_id,
            reason_code=ScanIssueCode.NO_TEXT,
        )
        return
    for index, parsed in enumerate(parsed_rows):
        last = index == len(parsed_rows) - 1
        yield ScannedEvidence(
            cursor=cursor if last and advance else before,
            evidence=to_evidence(
                source_id=source_id,
                source_file=source_file,
                parsed=parsed,
                ordinal=f"{source_file.safe_id}:row:{source_rowid}:{index}",
                source_version=source_version,
            ),
        )


def _has_columns(
    connection: sqlite3.Connection,
    table: str,
    required: set[str],
) -> bool:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if exists is None:
        return False
    actual = {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}
    return required.issubset(actual)


def _json_object(value: object) -> dict[str, JsonValue] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _timestamp_value(value: object) -> str | int | float | None:
    return value if isinstance(value, (str, int, float)) else None


def _message_parts(value: object) -> list[tuple[str, bool, str]]:
    """A message's text and tool parts in order, as `(part id, is tool, text)`.

    OpenCode's `ToolPart` is `{type: "tool", tool, callID, state: {input, output, error}}`.
    """
    if not isinstance(value, str):
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    found: list[tuple[str, bool, str]] = []
    for item in parsed if isinstance(parsed, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("data"), dict):
            continue
        if not isinstance(item.get("id"), str):
            continue
        data = item["data"]
        if data.get("type") == "text":
            text = data.get("text")
            if isinstance(text, str) and text.strip():
                found.append((item["id"], False, text.strip()))
            continue
        state = data.get("state") if isinstance(data.get("state"), dict) else {}
        tool_text = tool_evidence_text(
            {
                "name": data.get("tool"),
                "output": state.get("output") or state.get("error"),
                "input": state.get("input"),
            }
        )
        if tool_text is not None:
            found.append((item["id"], True, tool_text))
    return found


def _v2_message_parts(
    message_data: dict[str, JsonValue] | None,
    role: str,
    message_id: str,
) -> list[tuple[str, bool, str]]:
    """Text and tool content of a V2 message, in order.

    V2 folds a whole message into one row: `user` carries `text`, `assistant`
    carries `content` as a list of text, reasoning and tool items. Reasoning is
    neither the person's words nor the answer, so it stays out.
    """
    if message_data is None:
        return []
    if role == "user":
        text = message_data.get("text")
        if isinstance(text, str) and text.strip():
            return [(message_id, False, text.strip())]
        return []
    content = message_data.get("content")
    if not isinstance(content, list):
        return []
    found: list[tuple[str, bool, str]] = []
    text_index = 0
    for item in content:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "text":
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                part_id = message_id if text_index == 0 else f"{message_id}:text:{text_index}"
                found.append((part_id, False, text.strip()))
                text_index += 1
            continue
        if kind != "tool":
            continue
        raw_state = item.get("state")
        state: dict[str, JsonValue] = raw_state if isinstance(raw_state, dict) else {}
        tool_id = item.get("id")
        part_id = tool_id if isinstance(tool_id, str) else f"{message_id}:tool:{len(found)}"
        tool_text = tool_evidence_text(
            {
                "name": item.get("name") or item.get("tool"),
                "output": _v2_tool_output(state),
                "input": state.get("input"),
            }
        )
        if tool_text is not None:
            found.append((part_id, True, tool_text))
    return found


def _v2_tool_output(state: dict[str, JsonValue]) -> JsonValue | None:
    content = state.get("content")
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
        joined = "\n".join(texts).strip()
        if joined:
            return joined
    return state.get("output") or state.get("error")


def _assistant_settled(
    message_data: dict[str, JsonValue] | None,
    time_updated: object,
    now_ms: float,
) -> bool:
    """Whether an assistant message has stopped changing.

    OpenCode 2 inserts the message first and updates it in place while it
    streams, so a row read early holds half a reply. `time.completed` marks the
    end; a row that never got one was interrupted, and after the quiet window
    its visible text is final enough to keep.
    """
    if message_data is not None:
        timing = message_data.get("time")
        if isinstance(timing, dict) and timing.get("completed") is not None:
            return True
    updated_ms = _milliseconds(time_updated)
    return updated_ms is None or now_ms - updated_ms >= _OPENCODE_SETTLE_MS


def _milliseconds(value: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    # V2 columns are milliseconds; accept epoch seconds too so a schema that
    # stores seconds does not read as permanently unfinished.
    return float(value) * (1000 if value < 100_000_000_000 else 1)


def _row_checkpoint(checkpoint: str | None) -> int:
    if checkpoint is None or not checkpoint.startswith("row:"):
        return 0
    try:
        return max(0, int(checkpoint.removeprefix("row:")))
    except ValueError:
        return 0


def _validated_row_checkpoint(
    connection: sqlite3.Connection,
    *,
    table: str,
    last_rowid: int,
    checkpoint_anchor: str | None,
) -> int:
    if last_rowid == 0:
        return 0
    row = connection.execute(
        f'SELECT id FROM "{table}" WHERE rowid = ?',
        (last_rowid,),
    ).fetchone()
    if (
        row is None
        or checkpoint_anchor is None
        or _checkpoint_anchor(str(row["id"])) != checkpoint_anchor
    ):
        return 0
    return last_rowid


def _checkpoint_anchor(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
