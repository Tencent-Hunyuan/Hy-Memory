import html

from pydantic import JsonValue

from one_memory.imports.access import AuthorizedSourceRoot
from one_memory.imports.adapters import (
    ScanIssueCode,
    SourceCapabilities,
    SourceDescriptor,
    StorageKind,
)
from one_memory.imports.models import OriginMetadata, WrittenBy
from one_memory.imports.sources.common import (
    JsonlSourceAdapter,
    ParsedMessage,
    ParsedSkip,
    SourceRecordContext,
)
from one_memory.imports.sources.parsing import (
    as_record,
    conversation_from_relative_path,
    first_string,
    parse_timestamp,
    strip_host_wrappers,
    text_content,
    tool_evidence_text,
)

CODEX_DESCRIPTOR = SourceDescriptor(
    source_id="codex",
    display_name="Codex",
    storage_kinds=frozenset({StorageKind.JSONL}),
    capabilities=SourceCapabilities(
        can_append=True,
        can_observe_edits=True,
        can_observe_deletes=False,
        can_reconcile_complete_set=False,
    ),
)
HARNESS_PREFIXES = (
    "<codex_internal_context",
    "<environment_context>",
    "<permissions instructions>",
    "<system-reminder>",
    "<instructions>",
    "# agents.md instructions",
)
NESTED_INTERNAL_ENVELOPES = ("<codex_internal_context", "<heartbeat")
_USER_SESSION_SOURCES = frozenset({"cli", "vscode", "mcp", "exec"})
_SESSION_PROVENANCE_FIELDS = (
    "id",
    "session_id",
    "parent_thread_id",
    "originator",
    "cli_version",
    "client_version",
    "source",
    "thread_source",
    "agent_nickname",
    "agent_role",
)


def create_codex_adapter(
    grant: AuthorizedSourceRoot,
    *,
    source_version: str | None = None,
) -> JsonlSourceAdapter:
    return JsonlSourceAdapter(
        descriptor=CODEX_DESCRIPTOR,
        grant=grant,
        parser=parse_codex_record,
        source_version=source_version,
        file_predicate=lambda source_file: source_file.relative_path.name.startswith("rollout-"),
        header_selector=_is_session_header,
    )


def _is_session_header(record: dict[str, JsonValue]) -> bool:
    return record.get("type") == "session_meta" and as_record(record.get("payload")) is not None


def _is_internal_envelope(value: str) -> bool:
    text = html.unescape(value).lstrip().casefold()
    return text.startswith(HARNESS_PREFIXES) or any(
        marker in text for marker in NESTED_INTERNAL_ENVELOPES
    )


def parse_codex_record(
    record: dict[str, JsonValue],
    context: SourceRecordContext,
) -> ParsedMessage | ParsedSkip:
    if record.get("type") != "response_item":
        return ParsedSkip(ScanIssueCode.UNKNOWN_RECORD)
    payload = as_record(record.get("payload"))
    if payload is None:
        return ParsedSkip(ScanIssueCode.UNKNOWN_RECORD)
    if payload.get("type") == "reasoning":
        return ParsedSkip(ScanIssueCode.INTERNAL_REASONING_EXCLUDED)
    if payload.get("type") != "message":
        tool_text = tool_evidence_text(payload)
        if tool_text is None:
            return ParsedSkip(ScanIssueCode.TOOL_CONTENT_EXCLUDED)
        if _is_internal_envelope(tool_text):
            return ParsedSkip(ScanIssueCode.INTERNAL_REASONING_EXCLUDED)
        header = context.header or {}
        session = (as_record(header.get("payload")) or {}) if _is_session_header(header) else {}
        return ParsedMessage(
            conversation_id=conversation_from_relative_path(context.relative_path),
            message_id=first_string(payload.get("id"), payload.get("call_id"), record.get("id")),
            role="tool",
            written_by=WrittenBy.TOOL,
            created_at=parse_timestamp(record.get("timestamp")),
            verbatim=tool_text,
            workspace=first_string(session.get("cwd")),
            record_type=f"response_item.{first_string(payload.get('type')) or 'tool'}",
            origin_metadata=_origin_metadata(session, {}),
        )
    role = first_string(payload.get("role"))
    if role in {"developer", "system"}:
        return ParsedSkip(ScanIssueCode.INTERNAL_REASONING_EXCLUDED)
    if role not in {"user", "assistant"}:
        return ParsedSkip(ScanIssueCode.UNKNOWN_RECORD)
    content = text_content(
        payload.get("content"),
        allowed_block_types=frozenset({"text", "input_text", "output_text"}),
    )
    if not content:
        return ParsedSkip(ScanIssueCode.NO_TEXT)
    if _is_internal_envelope(content):
        return ParsedSkip(ScanIssueCode.INTERNAL_REASONING_EXCLUDED)
    header = context.header or {}
    session = (as_record(header.get("payload")) or {}) if _is_session_header(header) else {}
    message_provenance: dict[str, JsonValue] = {
        location: {"written_by": value["written_by"]}
        for location, value in (("record", record), ("payload", payload))
        if "written_by" in value
    }
    written_by = WrittenBy.AGENT
    if role == "user":
        written_by = _user_author(record, payload, session)
        # Machine inputs may quote a whole transcript, including user_query tags.
        # Protocol role is authoritative; host author metadata does not change
        # how a user message is cleaned.
        if not (
            content.startswith(
                "The following is the Codex agent history whose request action you are assessing."
            )
            and ">>> TRANSCRIPT START" in content
            and ">>> TRANSCRIPT END" in content
        ):
            content = strip_host_wrappers(content)
        if not content:
            return ParsedSkip(ScanIssueCode.NO_TEXT)
    return ParsedMessage(
        conversation_id=conversation_from_relative_path(context.relative_path),
        message_id=first_string(payload.get("id"), record.get("id")),
        role=role,
        written_by=written_by,
        created_at=parse_timestamp(record.get("timestamp")),
        verbatim=content,
        workspace=first_string(session.get("cwd")),
        record_type="response_item.message",
        origin_metadata=_origin_metadata(session, message_provenance),
    )


def _user_author(
    record: dict[str, JsonValue],
    payload: dict[str, JsonValue],
    session: dict[str, JsonValue],
) -> WrittenBy:
    authors: set[WrittenBy] = set()
    for value in (record, payload):
        if "written_by" not in value:
            continue
        author = value["written_by"]
        try:
            authors.add(WrittenBy(author) if isinstance(author, str) else WrittenBy.UNKNOWN)
        except ValueError:
            authors.add(WrittenBy.UNKNOWN)
    if authors:
        return next(iter(authors)) if len(authors) == 1 else WrittenBy.UNKNOWN
    source = session.get("source")
    if session.get("thread_source") in ("subagent", "guardian_review"):
        return WrittenBy.UNKNOWN
    if source is not None and not (isinstance(source, str) and source in _USER_SESSION_SOURCES):
        return WrittenBy.UNKNOWN
    return WrittenBy.PERSON


def _origin_metadata(
    session: dict[str, JsonValue],
    message_provenance: dict[str, JsonValue],
) -> OriginMetadata:
    # Do not replicate base instructions, git state or absolute paths per message.
    raw_meta: dict[str, JsonValue] = {}
    session_meta = {key: session[key] for key in _SESSION_PROVENANCE_FIELDS if key in session}
    if session_meta:
        raw_meta["session_meta"] = session_meta
    native_id = first_string(session.get("id"), session.get("session_id"))
    if native_id:
        # The filename identity remains authoritative until a migration can merge
        # rollovers without duplicating evidence or invalidating its references.
        raw_meta["native_session_id"] = native_id
    if message_provenance:
        raw_meta["message_provenance"] = message_provenance
    version = first_string(session.get("cli_version"), session.get("client_version"))
    return OriginMetadata(
        source_version=version if version is not None and len(version) <= 128 else None,
        raw_meta=raw_meta,
    )
