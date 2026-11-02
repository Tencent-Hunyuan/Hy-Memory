import json
import re
import sqlite3
from collections.abc import AsyncIterator, Iterable, Iterator
from functools import partial
from pathlib import Path, PurePosixPath

from pydantic import JsonValue

from one_memory.derived_echo import strip_injected_memory
from one_memory.imports.access import (
    AuthorizedSourceFile,
    AuthorizedSourceRoot,
    safe_project_label,
)
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
    SourceDescriptor,
    StorageKind,
)
from one_memory.imports.async_bridge import iterate_in_worker
from one_memory.imports.discovery import (
    SourceFileDiscoveryFailure,
    discovered_off_loop,
)
from one_memory.imports.models import WrittenBy
from one_memory.imports.readers import RawReadFailure, read_jsonl
from one_memory.imports.sources.common import (
    ParsedMessage,
    ParsedSkip,
    SourceRecordContext,
    discovery_failure_code,
    followed_by_tool,
    resume_checkpoint,
    row_ordinal,
    source_cursor,
    to_evidence,
)
from one_memory.imports.sources.parsing import (
    as_record,
    first_string,
    parse_timestamp,
    text_content,
    tool_blocks_evidence,
    tool_evidence_text,
)
from one_memory.imports.sqlite_access import open_sqlite_readonly

HERMES_DESCRIPTOR = SourceDescriptor(
    source_id="hermes",
    display_name="Hermes",
    storage_kinds=frozenset({StorageKind.JSONL, StorageKind.SQLITE}),
    capabilities=SourceCapabilities(
        can_append=True,
        can_observe_edits=False,
        can_observe_deletes=False,
        can_reconcile_complete_set=False,
    ),
)


class HermesSourceAdapter:
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
            suffixes=frozenset({".jsonl", ".db"}),
            max_targets=10_000,
            excluded_directory_names=frozenset({HERMES_PROFILES_DIRECTORY}),
        ):
            if isinstance(item, AuthorizedSourceFile) and _is_hermes_target(
                item.relative_path.as_posix()
            ):
                return True
        return False

    async def scan(self, options: ScanOptions) -> AsyncIterator[ScanEvent]:
        emitted = 0
        async for discovered in discovered_off_loop(
            self.grant,
            suffixes=frozenset({".jsonl", ".db"}),
            max_targets=options.max_scan_targets or 100_000,
            excluded_directory_names=frozenset({HERMES_PROFILES_DIRECTORY}),
        ):
            yield ScanProgressEvent(
                phase=ScanPhase.DISCOVER,
                discovered_increment=1,
            )
            if isinstance(discovered, SourceFileDiscoveryFailure):
                yield ScanFailure(reason_code=discovery_failure_code(discovered))
                continue
            if not _is_hermes_target(discovered.relative_path.as_posix()):
                continue
            try:
                if discovered.relative_path.name == "state.db":
                    event_factory = partial(
                        _read_state_db,
                        discovered,
                        options,
                        self.source_version,
                    )
                else:
                    event_factory = partial(
                        _read_rollout,
                        discovered,
                        options,
                        self.source_version,
                    )
                async for event in iterate_in_worker(
                    event_factory, scope_key=self.descriptor.source_id
                ):
                    yield event
                    if isinstance(event, ScannedEvidence):
                        emitted += 1
                        if options.max_messages is not None and emitted >= options.max_messages:
                            return
            except sqlite3.Error:
                yield ScanFailure(reason_code=ScanIssueCode.SOURCE_BUSY)
        yield ScanProgressEvent(phase=ScanPhase.DONE)


def create_hermes_adapter(
    grant: AuthorizedSourceRoot,
    *,
    source_version: str | None = None,
) -> HermesSourceAdapter:
    return HermesSourceAdapter(
        descriptor=HERMES_DESCRIPTOR,
        grant=grant,
        source_version=source_version,
    )


def parse_hermes_rollout_record(
    record: dict[str, JsonValue],
    context: SourceRecordContext,
) -> ParsedMessage | ParsedSkip:
    payload = as_record(record.get("payload"))
    nested = as_record(record.get("message"))
    if (
        record.get("type") == "response_item"
        and payload is not None
        and payload.get("type") == "message"
    ):
        message = payload
    elif record.get("type") == "message" or isinstance(record.get("role"), str):
        message = record
    elif payload is not None and (
        payload.get("type") == "message" or isinstance(payload.get("role"), str)
    ):
        message = payload
    elif nested is not None:
        message = nested
    else:
        return ParsedSkip(reason_code=ScanIssueCode.UNKNOWN_RECORD)
    role = first_string(message.get("role"))
    conversation_id = (
        first_string(message.get("conversationId"), message.get("sessionId"))
        or first_string(record.get("conversationId"), record.get("sessionId"))
        or PurePosixPath(context.relative_path).stem
    )
    if role == "tool":
        content = message.get("content")
        if isinstance(content, list):
            content = text_content(content, allowed_block_types=frozenset({"text"}))
        tool_text = tool_evidence_text({**message, "content": content})
        if tool_text is None:
            return ParsedSkip(
                reason_code=ScanIssueCode.TOOL_CONTENT_EXCLUDED,
                conversation_id=conversation_id,
            )
        return ParsedMessage(
            conversation_id=conversation_id,
            message_id=first_string(
                message.get("messageId"), message.get("id"), message.get("tool_call_id")
            ),
            role="tool",
            written_by=WrittenBy.TOOL,
            created_at=parse_timestamp(message.get("createdAt") or message.get("timestamp")),
            verbatim=tool_text,
            workspace=first_string(record.get("cwd"), record.get("workspacePath")),
            record_type="message.tool",
        )
    if role == "system":
        return ParsedSkip(
            reason_code=ScanIssueCode.INTERNAL_REASONING_EXCLUDED,
            conversation_id=conversation_id,
        )
    if role not in {"user", "assistant"}:
        return ParsedSkip(
            reason_code=ScanIssueCode.UNKNOWN_RECORD,
            conversation_id=conversation_id,
        )
    text = text_content(
        message.get("content"),
        allowed_block_types=frozenset({"text", "input_text", "output_text"}),
    )
    text = strip_injected_memory(text or "")
    if role == "user":
        text, injected = gateway_user_text(text)
        if injected is not None:
            return ParsedSkip(reason_code=injected, conversation_id=conversation_id)
    if not text:
        return ParsedSkip(
            reason_code=ScanIssueCode.NO_TEXT,
            conversation_id=conversation_id,
        )
    workspace = first_string(
        record.get("cwd"),
        record.get("workspacePath"),
        payload.get("cwd") if payload else None,
    )
    return ParsedMessage(
        conversation_id=conversation_id,
        message_id=first_string(message.get("messageId"), message.get("id")),
        role=role,
        written_by=WrittenBy.UNKNOWN if role == "user" else WrittenBy.AGENT,
        created_at=parse_timestamp(
            message.get("createdAt")
            or message.get("timestamp")
            or record.get("createdAt")
            or record.get("timestamp")
        ),
        verbatim=text,
        workspace=workspace,
        record_type="message",
    )


#: Hermes keeps each extra profile under `<root>/profiles/<name>/`, with its
#: own `state.db` and sessions. A profile is another person's history: it is
#: read only through its own grant (its own MemHub user), never as part of the
#: root above it. Hermes' own export skips this directory for the same reason.
#:
#: Skipped at any depth, not only directly under the root: a root granted
#: above the Hermes home holds `.hermes/profiles/`. Hermes keeps nothing of
#: its own under a directory of that name (its history is `state.db` and
#: `sessions/`), so nothing real is lost.
HERMES_PROFILES_DIRECTORY = "profiles"


def _is_hermes_target(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if HERMES_PROFILES_DIRECTORY in path.parts[:-1]:
        return False
    return path.name == "state.db" or (path.suffix == ".jsonl" and "sessions" in path.parts)


def hermes_root_is_profiles_container(root: Path) -> bool:
    """`profiles` itself holds everyone's profiles; it is never one person's history."""
    return root.name == HERMES_PROFILES_DIRECTORY


def hermes_root_conflict(mine: Path, other_users_roots: Iterable[Path]) -> str | None:
    """Why the resolved root `mine` may not be granted beside other users' Hermes roots.

    Paths are compared resolved (symlinks followed; the other users' roots are
    resolved here). Two roots that are equal or nest would read the same files
    for two users, except when one is exactly the other's `profiles/<name>`:
    the scan above never enters `profiles/`, so the two stay apart (`local` on
    `~/.hermes`, a user on `~/.hermes/profiles/<name>`). None when it may.
    """
    if hermes_root_is_profiles_container(mine):
        return HERMES_ROOT_IS_PROFILES_CONTAINER
    for other in other_users_roots:
        theirs = _real(other)
        if mine == theirs:
            return HERMES_ROOT_OVERLAPS_OTHER_USER
        for parent, child in ((mine, theirs), (theirs, mine)):
            if parent in child.parents:
                relative = child.relative_to(parent).parts
                if len(relative) != 2 or relative[0] != HERMES_PROFILES_DIRECTORY:
                    return HERMES_ROOT_OVERLAPS_OTHER_USER
    return None


#: 422: the root is the `profiles` container, not a profile.
HERMES_ROOT_IS_PROFILES_CONTAINER = "hermes_root_is_profiles_container"
#: 409: the root is, holds or sits inside another user's Hermes root.
HERMES_ROOT_OVERLAPS_OTHER_USER = "hermes_root_overlaps_other_user"


def _real(path: Path) -> Path:
    try:
        return path.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return path.expanduser().absolute()


def _read_rollout(
    source_file: AuthorizedSourceFile,
    options: ScanOptions,
    source_version: str | None,
) -> Iterator[ScanEvent]:
    checkpoint = resume_checkpoint(source_file, options)
    for raw in read_jsonl(source_file, checkpoint=checkpoint):
        cursor = source_cursor(
            source_file,
            raw.checkpoint,
            checkpoint_anchor=raw.checkpoint_anchor,
        )
        if isinstance(raw, RawReadFailure):
            yield ScanFailure(
                cursor=cursor,
                reason_code=ScanIssueCode.INVALID_JSON,
            )
            continue
        context = SourceRecordContext(
            file_key=source_file.safe_id,
            relative_path=source_file.relative_path.as_posix(),
            ordinal=f"{source_file.safe_id}:{raw.checkpoint}",
        )
        parsed = parse_hermes_rollout_record(raw.value, context)
        if isinstance(parsed, ParsedSkip):
            yield ScanSkip(
                cursor=cursor,
                conversation_id=parsed.conversation_id,
                reason_code=parsed.reason_code,
                report_issue=parsed.report_issue,
            )
            continue
        yield ScannedEvidence(
            cursor=cursor,
            evidence=to_evidence(
                source_id="hermes",
                source_file=source_file,
                parsed=parsed,
                ordinal=context.ordinal,
                source_version=source_version,
            ),
        )


def _read_state_db(
    source_file: AuthorizedSourceFile,
    options: ScanOptions,
    source_version: str | None,
) -> Iterator[ScanEvent]:
    checkpoint = resume_checkpoint(source_file, options)
    last_id = _row_checkpoint(checkpoint)
    with open_sqlite_readonly(source_file) as connection:
        columns = _columns(connection, "messages")
        required = {"id", "session_id", "role", "content", "timestamp"}
        if not required.issubset(columns):
            yield ScanFailure(reason_code=ScanIssueCode.UNSUPPORTED_SCHEMA)
            return
        select = {
            "platform_message_id": "NULL",
            "active": "NULL",
            "tool_name": "NULL",
            "tool_calls": "NULL",
        }
        for name in select:
            if name in columns:
                select[name] = f'"{name}"'
        session_columns = _columns(connection, "sessions")
        # The `sessions` row is where Hermes records who opened the
        # conversation. Without it a `user` row is only a role, which is why
        # every turn used to import as UNKNOWN and contribute no memory.
        session_select, session_join = "NULL AS session_source, NULL AS session_cwd", ""
        if {"id", "source"}.issubset(session_columns):
            cwd_column = '"cwd"' if "cwd" in session_columns else "NULL"
            session_select = f'sessions."source" AS session_source, {cwd_column} AS session_cwd'
            session_join = "LEFT JOIN sessions ON sessions.id = messages.session_id"
        rows = connection.execute(
            f"""
            SELECT messages.id AS id, messages.session_id AS session_id,
                   messages.role AS role, messages.content AS content,
                   messages.timestamp AS timestamp,
                   {select["platform_message_id"]} AS platform_message_id,
                   {select["active"]} AS active,
                   {select["tool_name"]} AS tool_name,
                   {select["tool_calls"]} AS tool_calls,
                   {session_select}
            FROM messages
            {session_join}
            WHERE messages.id > ? AND messages.role IN ('user', 'assistant', 'tool')
            ORDER BY messages.id
            """,
            (last_id,),
        )
        previous_id = last_id
        for row in rows:
            before = source_cursor(source_file, f"row:{previous_id}")
            previous_id = row["id"]
            cursor = source_cursor(source_file, f"row:{row['id']}")
            role = row["role"]
            if row["active"] == 0:
                yield ScanSkip(
                    cursor=cursor,
                    reason_code=ScanIssueCode.UNKNOWN_RECORD,
                )
                continue
            message_id = row["platform_message_id"] or f"{row['session_id']}:{row['id']}"
            if role == "tool":
                tool_text = tool_evidence_text(
                    {"tool_name": row["tool_name"], "content": row["content"]}
                )
                if tool_text is None:
                    yield ScanSkip(
                        cursor=cursor,
                        conversation_id=str(row["session_id"]),
                        reason_code=ScanIssueCode.TOOL_CONTENT_EXCLUDED,
                    )
                    continue
                workspace = first_string(row["session_cwd"])
                yield ScannedEvidence(
                    cursor=cursor,
                    evidence=to_evidence(
                        source_id="hermes",
                        source_file=source_file,
                        parsed=ParsedMessage(
                            conversation_id=str(row["session_id"]),
                            message_id=str(message_id),
                            role="tool",
                            written_by=WrittenBy.TOOL,
                            created_at=parse_timestamp(row["timestamp"]),
                            verbatim=tool_text,
                            workspace=workspace,
                            project=safe_project_label(workspace),
                            record_type="state_db_message.tool",
                        ),
                        ordinal=f"{source_file.safe_id}:row:{row['id']}",
                        source_version=source_version,
                    ),
                )
                continue
            # The assistant's call sits on its own row as `tool_calls`; the
            # `tool` rows after it hold only what came back.
            calls = _tool_calls_text(row["tool_calls"]) if role == "assistant" else None
            content = strip_injected_memory(str(row["content"] or ""))
            if role == "user":
                content, injected = gateway_user_text(content)
                if injected is not None:
                    yield ScanSkip(
                        cursor=cursor,
                        conversation_id=str(row["session_id"]),
                        reason_code=injected,
                    )
                    continue
            has_text = bool(content.strip())
            if role not in {"user", "assistant"} or (not has_text and calls is None):
                yield ScanSkip(
                    cursor=cursor,
                    conversation_id=str(row["session_id"]),
                    reason_code=ScanIssueCode.NO_TEXT,
                )
                continue
            workspace = first_string(row["session_cwd"])
            parsed = ParsedMessage(
                conversation_id=str(row["session_id"]),
                message_id=str(message_id),
                role=str(role) if has_text else "tool",
                written_by=_written_by(role, row["session_source"]) if has_text else WrittenBy.TOOL,
                created_at=parse_timestamp(row["timestamp"]),
                verbatim=content if has_text else str(calls),
                workspace=workspace,
                project=safe_project_label(workspace),
                record_type="state_db_message" if has_text else "state_db_message.tool",
            )
            rows_here = followed_by_tool(parsed, calls).rows() if has_text else (parsed,)
            for index, item in enumerate(rows_here):
                yield ScannedEvidence(
                    # A stop between two rows of one message reads the message again.
                    cursor=cursor if index == len(rows_here) - 1 else before,
                    evidence=to_evidence(
                        source_id="hermes",
                        source_file=source_file,
                        parsed=item,
                        ordinal=row_ordinal(f"{source_file.safe_id}:row:{row['id']}", index),
                        source_version=source_version,
                    ),
                )


#: Session origins where a person is the one typing. Read off the installed
#: 0.21.0 checkout, whose own `sessions.source` vocabulary also includes
#: machine-opened origins — `cron` for its scheduler, `delegate` for subagent
#: hand-offs, plus `api_server` and `kanban`. Hermes stores those the same
#: way, with the machine's instruction wearing the `user` role.
#:
#: This is an allowlist rather than a denylist on purpose. A source we have
#: not seen imports as UNKNOWN and contributes nothing, which loses evidence;
#: a denylist would instead attribute an unseen machine origin to the person,
#: which pollutes memory. Messaging origins (`telegram`, `discord`, and the
#: rest of the gateway) are deliberately left out until a real session shows
#: whether a bot's own posts land in the same table wearing the `user` role.
_PERSON_SESSION_SOURCES = frozenset({"cli", "tui", "webui", "desktop"})


#: What the Hermes gateway writes into a `user` row on its own behalf. Every
#: one of these wears the `user` role, so only the text can tell them apart
#: from the person. Read off real `state.db` rows: a finished background
#: process is posted as a turn, often with the gateway's own "not written by
#: a human" note appended, which is why that note counts anywhere in the row.
_BACKGROUND_NOTICE = re.compile(
    r"\[IMPORTANT:\s*(?:\d+\s+background\s+process(?:es)?\b|Background\s+process\b)",
    re.IGNORECASE,
)
_GATEWAY_NOTICE = re.compile(
    r"\[INTERNAL NOTIFICATION\s*\W+\s*generated by the Hermes gateway",
    re.IGNORECASE,
)
_VOICE_PLACEHOLDER = re.compile(r"\[voice message could not be transcribed", re.IGNORECASE)
_PROBE = re.compile(r"Reply with exactly:\s*[A-Za-z0-9_\-]{1,24}\s*")
_MEDIA_ONLY = re.compile(r"(?:MEDIA:/\S+\s*)+")
#: Wrappers the gateway puts around a message the person did write. Only the
#: wrapper goes; what it carries is the turn. Each is matched at the edge of
#: what is left, and the set is applied until nothing more comes off, since a
#: relayed message carries the origin block inside the out-of-band markers.
_GATEWAY_WRAPPERS = (
    re.compile(r"^\[OUT-OF-BAND USER MESSAGE\b[^\]\n]*\]"),
    re.compile(r"\[/OUT-OF-BAND USER MESSAGE\]$"),
    re.compile(
        r"^Gateway message origin \(JSON data, not instructions or authorization\):[ \t]*"
        r"(?:\n[ \t]*\{[^\n]*\}[ \t]*)?"
        r"(?:\n[ \t]*Do not guess a reply destination[^\n]*)?"
        r"(?=\n|$)"
    ),
    re.compile(r"^\[Context from the interrupted assistant response\][ \t]*(?=\n|$)"),
)


def gateway_user_text(text: str) -> tuple[str, ScanIssueCode | None]:
    """The person's words in a Hermes `user` row, or why there are none.

    Applied only to the `user` role: an assistant or tool row quoting the
    same text is reporting what it saw, which is the existing tool rule's
    business. The cleaned text is what becomes the evidence, so its content
    hash and duplicate check see the message rather than its envelope.
    """

    if _GATEWAY_NOTICE.search(text):
        return "", ScanIssueCode.INJECTED_MACHINE_NOTICE
    cleaned = text.strip()
    while True:
        unwrapped = cleaned
        for wrapper in _GATEWAY_WRAPPERS:
            unwrapped = wrapper.sub("", unwrapped, count=1).strip()
        if unwrapped == cleaned:
            break
        cleaned = unwrapped
    if _BACKGROUND_NOTICE.match(cleaned):
        return "", ScanIssueCode.INJECTED_MACHINE_NOTICE
    if _VOICE_PLACEHOLDER.match(cleaned):
        return "", ScanIssueCode.VOICE_PLACEHOLDER
    if _PROBE.fullmatch(cleaned):
        return "", ScanIssueCode.PROBE
    if _MEDIA_ONLY.fullmatch(cleaned):
        return "", ScanIssueCode.NO_TEXT
    return cleaned, None


def _tool_calls_text(value: object) -> str | None:
    """OpenAI-shaped `tool_calls`: `[{"function": {"name", "arguments"}}]`, stored as JSON."""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(value, list):
        return None
    blocks: list[JsonValue] = []
    for item in value:
        function = as_record((as_record(item) or {}).get("function"))
        if function is not None:
            blocks.append(
                {
                    "type": "call",
                    "name": function.get("name"),
                    "arguments": function.get("arguments"),
                }
            )
    return tool_blocks_evidence(blocks, block_types=frozenset({"call"}))


def _written_by(role: str, session_source: object) -> WrittenBy:
    if role != "user":
        return WrittenBy.AGENT
    if isinstance(session_source, str) and session_source in _PERSON_SESSION_SOURCES:
        return WrittenBy.PERSON
    return WrittenBy.UNKNOWN


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if exists is None:
        return set()
    return {str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')}


def _row_checkpoint(checkpoint: str | None) -> int:
    if checkpoint is None:
        return 0
    kind, separator, value = checkpoint.partition(":")
    if kind != "row" or separator != ":":
        return 0
    return max(0, int(value))
