import hashlib
import json
import sqlite3
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from functools import partial

from one_memory.imports.access import AuthorizedSourceFile, AuthorizedSourceRoot
from one_memory.imports.adapters import (
    ScanCursorTouch,
    ScanEvent,
    ScanFailure,
    ScanIssueCode,
    ScannedEvidence,
    ScanOptions,
    ScanPhase,
    ScanProgressEvent,
    ScanSkip,
    SourceCapabilities,
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
    touched_cursor,
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
            last = None
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
                    cursor = getattr(event, "cursor", None)
                    if cursor is not None:
                        last = cursor
                    if isinstance(event, ScannedEvidence):
                        emitted += 1
                        if options.max_messages is not None and emitted >= options.max_messages:
                            return
            except sqlite3.Error:
                yield ScanFailure(reason_code=ScanIssueCode.SOURCE_BUSY)
                continue
            # Read to the end of this file. Without this touch the staged
            # records stay in `state='assembling'` forever: `finalize_sdk_file`
            # is only reached from the runner's ScanCursorTouch branch, and
            # `flush_idle_assemblies` skips `priority='normal'` (history).
            yield ScanCursorTouch(
                cursor=touched_cursor(
                    discovered,
                    options,
                    last=last,
                    empty_checkpoint="record:0",
                )
            )
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


def _read_opencode_db(
    source_file: AuthorizedSourceFile,
    options: ScanOptions,
    source_id: str,
    source_version: str | None,
) -> Iterator[ScanEvent]:
    checkpoint = resume_checkpoint(source_file, options)
    last_rowid = _row_checkpoint(checkpoint)
    previous = options.cursors.get(source_file.safe_id)
    with open_sqlite_readonly(source_file) as connection:
        if not all(
            _has_columns(connection, table, columns)
            for table, columns in (
                ("message", {"id", "session_id", "time_created", "data"}),
                ("part", {"id", "message_id", "time_created", "data"}),
                ("session", {"id", "directory"}),
            )
        ):
            yield ScanFailure(reason_code=ScanIssueCode.UNSUPPORTED_SCHEMA)
            return
        last_rowid = _validated_row_checkpoint(
            connection,
            last_rowid=last_rowid,
            checkpoint_anchor=(previous.checkpoint_anchor if previous is not None else None),
        )
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
            checkpoint_anchor=(
                previous.checkpoint_anchor if previous is not None and last_rowid else None
            ),
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
            workspace = row["directory"] if isinstance(row["directory"], str) else None
            created_at = parse_timestamp(_timestamp_value(row["time_created"]))
            # One message spans several steps: text, a tool call, more text.
            # Rows follow the parts in that order; each run of text is one row,
            # the first under the message's own id.
            parsed_rows: list[ParsedMessage] = []
            for part_id, is_tool, text in _message_parts(row["parts"]):
                if not is_tool and parsed_rows and parsed_rows[-1].role != "tool":
                    last_text = parsed_rows[-1]
                    parsed_rows[-1] = replace(last_text, verbatim=f"{last_text.verbatim}\n{text}")
                    continue
                first_text = not is_tool and not any(r.role != "tool" for r in parsed_rows)
                parsed_rows.append(
                    ParsedMessage(
                        conversation_id=conversation_id,
                        message_id=message_id if first_text else part_id,
                        role="tool" if is_tool else str(role),
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
                    cursor=cursor,
                    conversation_id=conversation_id,
                    reason_code=ScanIssueCode.NO_TEXT,
                )
                before = cursor
                continue
            for index, parsed in enumerate(parsed_rows):
                last = index == len(parsed_rows) - 1
                yield ScannedEvidence(
                    cursor=cursor if last else before,
                    evidence=to_evidence(
                        source_id=source_id,
                        source_file=source_file,
                        parsed=parsed,
                        ordinal=f"{source_file.safe_id}:row:{source_rowid}:{index}",
                        source_version=source_version,
                    ),
                )
            before = cursor


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


def _json_object(value: object) -> dict[str, object] | None:
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
    last_rowid: int,
    checkpoint_anchor: str | None,
) -> int:
    if last_rowid == 0:
        return 0
    row = connection.execute(
        "SELECT id FROM message WHERE rowid = ?",
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
