import stat
from pathlib import PurePosixPath

from pydantic import JsonValue

from one_memory.imports.access import (
    AuthorizedSourceFile,
    AuthorizedSourceRoot,
    safe_project_label,
)
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
    followed_by_tool,
)
from one_memory.imports.sources.parsing import (
    as_record,
    first_string,
    parse_timestamp,
    text_content,
    tool_blocks_evidence,
)

PI_DESCRIPTOR = SourceDescriptor(
    source_id="pi",
    display_name="Pi",
    storage_kinds=frozenset({StorageKind.JSONL}),
    capabilities=SourceCapabilities(
        can_append=True,
        can_observe_edits=False,
        can_observe_deletes=False,
        can_reconcile_complete_set=False,
    ),
)


def create_pi_adapter(
    grant: AuthorizedSourceRoot,
    *,
    descriptor: SourceDescriptor = PI_DESCRIPTOR,
    source_version: str | None = None,
) -> JsonlSourceAdapter:
    return JsonlSourceAdapter(
        descriptor=descriptor,
        grant=grant,
        parser=parse_pi_record,
        source_version=source_version,
        header_selector=_is_session_header,
        file_predicate=_include_pi_transcript,
    )


def _is_session_header(record: dict[str, JsonValue]) -> bool:
    return record.get("type") == "session"


#: Oh My Pi writes one session as `<cwd>/<session>.jsonl`, and every sub-agent
#: transcript into `<cwd>/<session>/<SubAgent>.jsonl`. The walk is recursive, so
#: without this predicate those transcripts are imported as conversations of
#: their own -- and their `user` turns are the parent agent assigning a task,
#: not the person speaking. A transcript is a sub-agent artifact exactly when
#: its parent directory has a sibling `<dir>.jsonl` session file.
#:
#: `lstat` rather than `is_file`: a symlink named like the session must not
#: turn an unrelated directory into an artifact folder.
def _is_subagent_transcript(source_file: AuthorizedSourceFile) -> bool:
    parent = source_file.path.parent
    session_file = parent.parent / f"{parent.name}.jsonl"
    try:
        details = session_file.lstat()
    except OSError:
        return False
    return stat.S_ISREG(details.st_mode)


def _include_pi_transcript(source_file: AuthorizedSourceFile) -> bool:
    return not _is_subagent_transcript(source_file)


#: Pi is invoked unattended by whatever embeds it, not only by a person, and
#: those invocations arrive wearing the `user` role. On this machine six of
#: eight `user` turns came from an hourly pipe and one from Pi's own title
#: generator; only one was somebody typing. The session's working directory is
#: what separates them: an automation runs in its own directory rather than in
#: a project.
#:
#: Such a session is not imported at all, either role of it: nobody in it is
#: the person, so the prompt is a robot's and so is the answer. Labelling it as
#: an agent turn would also keep it out of memory — the extraction gate reads
#: only `PERSON` — but it would still store a robot talking to itself, and
#: Qwen Work already held the stricter line for the same situation
#: (`SECRET_DO_NOT_IMPORT` in its test says so outright). Unifying toward the
#: stricter side means no guarantee is weakened to make two sources agree
#: (decision D-17).
_AUTOMATION_DIRECTORIES = frozenset({"pi-title"})
_AUTOMATION_PARENT = "pipes"


def _is_automated_session(workspace: str | None) -> bool:
    if workspace is None:
        return False
    path = PurePosixPath(workspace)
    return path.name in _AUTOMATION_DIRECTORIES or path.parent.name == _AUTOMATION_PARENT


def parse_pi_record(
    record: dict[str, JsonValue],
    context: SourceRecordContext,
) -> ParsedMessage | ParsedSkip:
    if record.get("type") == "session":
        return ParsedSkip(reason_code=ScanIssueCode.UNKNOWN_RECORD)
    if record.get("type") != "message":
        return ParsedSkip(reason_code=ScanIssueCode.UNKNOWN_RECORD)
    message = as_record(record.get("message"))
    if message is None:
        return ParsedSkip(reason_code=ScanIssueCode.UNKNOWN_RECORD)
    relative = PurePosixPath(context.relative_path)
    conversation_id = relative.stem
    # Pi states the working directory once, in the opening `session` record, and
    # never repeats it per message. Reading it off the message leaves every Pi
    # memory with no workspace and no project at all.
    workspace = first_string(
        record.get("cwd"),
        (context.header or {}).get("cwd"),
    )
    if _is_automated_session(workspace):
        return ParsedSkip(
            reason_code=ScanIssueCode.NOT_WRITTEN_BY_PERSON,
            conversation_id=conversation_id,
        )
    role = first_string(message.get("role"))
    if role in {"tool", "toolResult"}:
        tool_text = tool_blocks_evidence([{**message, "type": "toolResult"}], block_types=_TOOL)
        return _tool_message(record, message, conversation_id, workspace, tool_text)
    if role not in {"user", "assistant"}:
        return ParsedSkip(reason_code=ScanIssueCode.INTERNAL_REASONING_EXCLUDED)
    text = text_content(
        message.get("content"),
        allowed_block_types=frozenset({"text"}),
    )
    if not text and role == "assistant":
        tool_text = tool_blocks_evidence(message.get("content"), block_types=_TOOL)
        return _tool_message(record, message, conversation_id, workspace, tool_text)
    if not text:
        return ParsedSkip(reason_code=ScanIssueCode.NO_TEXT)
    return followed_by_tool(
        ParsedMessage(
            conversation_id=conversation_id,
            message_id=first_string(record.get("id"), record.get("messageId")),
            role=role,
            written_by=WrittenBy.PERSON if role == "user" else WrittenBy.AGENT,
            created_at=parse_timestamp(record.get("timestamp") or message.get("timestamp")),
            verbatim=text,
            workspace=workspace,
            project=safe_project_label(workspace),
            record_type="message",
        ),
        tool_blocks_evidence(message.get("content"), block_types=_TOOL)
        if role == "assistant"
        else None,
    )


#: `toolCall` blocks sit in assistant content; a `toolResult` is its own message.
_TOOL = frozenset({"toolCall", "toolResult"})


def _tool_message(
    record: dict[str, JsonValue],
    message: dict[str, JsonValue],
    conversation_id: str,
    workspace: str | None,
    tool_text: str | None,
) -> ParsedMessage | ParsedSkip:
    if tool_text is None:
        return ParsedSkip(reason_code=ScanIssueCode.TOOL_CONTENT_EXCLUDED)
    return ParsedMessage(
        conversation_id=conversation_id,
        message_id=first_string(record.get("id"), record.get("messageId")),
        role="tool",
        written_by=WrittenBy.TOOL,
        created_at=parse_timestamp(record.get("timestamp") or message.get("timestamp")),
        verbatim=tool_text,
        workspace=workspace,
        project=safe_project_label(workspace),
        record_type="message.tool",
    )
