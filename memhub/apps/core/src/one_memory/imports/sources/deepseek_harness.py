import re
from pathlib import PurePosixPath

from pydantic import JsonValue

from one_memory.imports.access import AuthorizedSourceRoot, safe_project_label
from one_memory.imports.adapters import (
    ScanIssueCode,
    SourceCapabilities,
    SourceDescriptor,
    StorageKind,
)
from one_memory.imports.models import WrittenBy
from one_memory.imports.sources.common import (
    JsonlSourceAdapter,
    ParsedMessage,
    ParsedSkip,
    SourceRecordContext,
)
from one_memory.imports.sources.parsing import (
    as_record,
    first_string,
    parse_timestamp,
    text_content,
    tool_blocks_evidence,
    tool_evidence_text,
)

DEEPSEEK_HARNESS_DESCRIPTOR = SourceDescriptor(
    source_id="deepseek-harness",
    display_name="DeepSeek Harness",
    storage_kinds=frozenset({StorageKind.JSONL, StorageKind.ZSTD}),
    capabilities=SourceCapabilities(
        can_append=True,
        can_observe_edits=False,
        can_observe_deletes=False,
        can_reconcile_complete_set=False,
    ),
)


#: One session is one file. Harness names the file after the format version it
#: wrote — `session.jsonl.zstd` for the first format, `session.v3.jsonl.zstd`
#: and `session.v4.jsonl.zstd` after that — so the version segment is part of
#: the published on-disk contract, not a variant to skip. The desktop profile
#: this was verified against writes v3/v4 exclusively, and the earlier literal
#: match imported none of those sessions.
#:
#: Discovery is deliberately wider than parsing: a `vN` file is found and then
#: judged by its header (see `SUPPORTED_SESSION_VERSIONS`), so an unknown
#: generation is reported as skipped instead of silently vanishing.
SESSION_FILE = re.compile(r"^session(?:\.v[0-9]+)?\.jsonl(?:\.zstd)?$")

#: Session generations this parser has been verified against: the first format
#: (header `version: 0`; its file name carries no `.vN` segment), v3 and v4.
#: The header states the generation, and the file name repeats it from v3 on.
#: A future generation may change field semantics, and importing it with
#: today's rules would store evidence that is harder to undo than a missed
#: import — so an unknown version is skipped as `unsupported_schema` and left
#: for a parser that knows it.
SUPPORTED_SESSION_VERSIONS = frozenset({0, 3, 4})


def create_deepseek_harness_adapter(
    grant: AuthorizedSourceRoot,
    *,
    descriptor: SourceDescriptor = DEEPSEEK_HARNESS_DESCRIPTOR,
    source_version: str | None = None,
) -> JsonlSourceAdapter:
    return JsonlSourceAdapter(
        descriptor=descriptor,
        grant=grant,
        parser=parse_deepseek_harness_record,
        source_version=source_version,
        file_predicate=lambda source_file: bool(SESSION_FILE.match(source_file.relative_path.name)),
        suffixes=frozenset({".jsonl", ".zstd"}),
        header_selector=_is_session_header,
    )


def _is_session_header(record: dict[str, JsonValue]) -> bool:
    """Harness writes the session's `cwd` once, in the first line only.

    Verified against a session this machine produced: the header is
    `{"type":"session","version":0,"id":...,"cwd":"/private/tmp/dsh-verify"}`
    and no later record repeats it. Read per-record, `cwd` is therefore always
    absent and every turn imports with no workspace.
    """
    return record.get("type") == "session"


def _supported_session(header: dict[str, JsonValue] | None) -> bool:
    """Whether the session's declared format generation is one we can read.

    The file name only selects the file for reading; the header states the
    generation this parser would have to interpret. A missing header, or a
    header without `version`, is treated as the first format — the earliest
    files this adapter already read. Any declared version outside
    `SUPPORTED_SESSION_VERSIONS` is refused rather than interpreted with
    today's field rules.
    """
    if header is None:
        return True
    version = header.get("version")
    if version is None:
        return True
    return isinstance(version, int) and version in SUPPORTED_SESSION_VERSIONS


def parse_deepseek_harness_record(
    record: dict[str, JsonValue],
    context: SourceRecordContext,
) -> ParsedMessage | ParsedSkip:
    relative = PurePosixPath(context.relative_path)
    conversation_id = (
        first_string((context.header or {}).get("id"))
        or relative.parent.name
        or relative.name.split(".", maxsplit=1)[0]
    )
    if not _supported_session(context.header):
        return ParsedSkip(
            reason_code=ScanIssueCode.UNSUPPORTED_SCHEMA,
            conversation_id=conversation_id,
        )
    event_type = record.get("type")
    data = as_record(record.get("data"))
    if event_type == "session":
        return ParsedSkip(reason_code=ScanIssueCode.UNKNOWN_RECORD)
    if data is None:
        return ParsedSkip(
            reason_code=ScanIssueCode.UNKNOWN_RECORD,
            conversation_id=conversation_id,
        )
    workspace = first_string(record.get("cwd"), (context.header or {}).get("cwd"))
    if event_type in {"tool/call", "tool/result"}:
        return _tool_message(record, data, conversation_id, workspace)
    message: dict[str, JsonValue] | None
    if event_type == "user/message":
        source = as_record(data.get("source"))
        if source is None or source.get("kind") != "user":
            return ParsedSkip(
                reason_code=ScanIssueCode.INTERNAL_REASONING_EXCLUDED,
                conversation_id=conversation_id,
            )
        message = data
    elif event_type == "assistant/message":
        message = as_record(data.get("message"))
        if message is None:
            return ParsedSkip(
                reason_code=ScanIssueCode.UNKNOWN_RECORD,
                conversation_id=conversation_id,
            )
    else:
        return ParsedSkip(
            reason_code=ScanIssueCode.INTERNAL_REASONING_EXCLUDED,
            conversation_id=conversation_id,
        )
    role = first_string(message.get("role"))
    if role not in {"user", "assistant"}:
        return ParsedSkip(
            reason_code=ScanIssueCode.TOOL_CONTENT_EXCLUDED,
            conversation_id=conversation_id,
        )
    text = text_content(
        message.get("content"),
        allowed_block_types=frozenset({"text"}),
    )
    if not text:
        return ParsedSkip(
            reason_code=ScanIssueCode.NO_TEXT,
            conversation_id=conversation_id,
        )
    return ParsedMessage(
        conversation_id=conversation_id,
        message_id=first_string(message.get("id"), message.get("messageId")),
        role=role,
        written_by=WrittenBy.PERSON if role == "user" else WrittenBy.AGENT,
        created_at=parse_timestamp(record.get("time") or message.get("timestamp")),
        verbatim=text,
        workspace=workspace,
        project=safe_project_label(workspace),
        record_type=str(event_type),
    )


def _tool_message(
    record: dict[str, JsonValue],
    data: dict[str, JsonValue],
    conversation_id: str,
    workspace: str | None,
) -> ParsedMessage | ParsedSkip:
    """`tool/call` carries name and arguments; `tool/result` a user-role message.

    The same call also appears as a `tool-call` block in `assistant/message`;
    only the event is read, so it is stored once.

    The result's content blocks are `tool-result` in the first session format
    and plain `text` in v4, the only version this shape was verified against;
    both carry the output, so both are evidence. A block of any other type
    (image, resource) is not.
    """
    message = as_record(data.get("message"))
    if record.get("type") == "tool/result" and message is not None:
        blocks = message.get("content")
        text = tool_blocks_evidence(blocks, block_types=frozenset({"tool-result"}))
        if text is None:
            # v4 writes the output as plain text blocks. Wrapping them in the
            # shape the helper reads keeps the same clipping and credential
            # scrubbing instead of writing a second extraction path.
            text = tool_blocks_evidence(
                [{"type": "tool-result", "content": blocks}],
                block_types=frozenset({"tool-result"}),
            )
        message_id = first_string(message.get("id"), data.get("callId"))
    else:
        text = tool_evidence_text(data)
        message_id = first_string(data.get("callId"), data.get("id"))
    if text is None:
        return ParsedSkip(
            reason_code=ScanIssueCode.TOOL_CONTENT_EXCLUDED,
            conversation_id=conversation_id,
        )
    return ParsedMessage(
        conversation_id=conversation_id,
        message_id=message_id,
        role="tool",
        written_by=WrittenBy.TOOL,
        created_at=parse_timestamp(record.get("time")),
        verbatim=text,
        workspace=workspace,
        project=safe_project_label(workspace),
        record_type=str(record.get("type")),
    )
