"""ZCode source: rollout transcripts plus the realtime capture channel.

ZCode keeps per-session model I/O under `~/.zcode/cli/rollout/` as
`model-io-<sessionId>.jsonl`, one record per model call: the request's whole
message history, the response's text, and the bookkeeping around them. A turn
spans many records, and every record repeats the messages before it, so the
parser cannot take "the last user message" per record — the same prompt would
be imported once per model call. A record's closing message is the anchor
instead: only the record whose request ends on a user message holds new input,
and the tool loop's later records end on tool results. The assistant side has
no such aliasing; a response that carries text is one message.

The stream is not append-only: compaction rewrites the file, which invalidates
byte cursors and forces a re-read. Idempotence then rests on message IDs keyed
by the call's `requestId`, so a re-read of the same calls files nothing twice.

One attribution limit: a prompt that closes a request is filed as the person's
words. ZCode records carry no author provenance (`querySource` distinguishes
only main turns from compaction), so a prompt a machine typed — a scripted
evaluation driving the host headlessly — is indistinguishable from the user's
own and lands as PERSON. Everything the host can say about authorship is used;
the rest is a limitation of the format, not of the parsing.
"""

from dataclasses import replace

from pydantic import JsonValue

from one_memory.derived_echo import strip_injected_memory
from one_memory.imports.access import AuthorizedSourceFile, AuthorizedSourceRoot
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
    conversation_from_relative_path,
    first_string,
    parse_timestamp,
    text_content,
    unwrap_host_envelope,
)

ZCODE_DESCRIPTOR = SourceDescriptor(
    source_id="zcode",
    display_name="ZCode",
    storage_kinds=frozenset({StorageKind.JSONL}),
    capabilities=SourceCapabilities(
        can_append=True,
        can_observe_edits=True,
        # A cleared ZCode session leaves no marker behind, so disappearances
        # cannot be told from "never existed" by scanning.
        can_observe_deletes=False,
        can_reconcile_complete_set=False,
    ),
)


def is_zcode_transcript(source_file: AuthorizedSourceFile) -> bool:
    """A rollout model-I/O stream, not one of the files sitting beside it.

    `~/.zcode` also holds config, skills, and logs that happen to end in
    text extensions; only `cli/rollout/model-io-*.jsonl` is per-session
    conversation data.
    """
    relative = source_file.relative_path
    return (
        relative.parent.as_posix() == "cli/rollout"
        and relative.name.startswith("model-io-")
        and relative.suffix == ".jsonl"
    )


def parse_zcode_record(
    record: dict[str, JsonValue],
    context: SourceRecordContext,
) -> ParsedMessage | ParsedSkip:
    conversation_id = first_string(record.get("sessionId")) or conversation_from_relative_path(
        context.relative_path
    )
    request = as_record(record.get("request"))
    response = as_record(record.get("response"))
    # A record this build cannot recognize is named as such, not quietly filed
    # under a skip reason that reads as ordinary exclusions: a renamed field or
    # a new record kind must surface as "imported nothing and here is why",
    # never as a green sync over an empty stream.
    if first_string(record.get("querySource")) not in {"main_turn", "compact"} or (
        request is None and response is None
    ):
        return ParsedSkip(ScanIssueCode.UNKNOWN_RECORD, conversation_id)
    # `compact` marks the context-compaction call that rewrites history for
    # the model; it is host bookkeeping, not a conversation turn.
    if first_string(record.get("querySource")) != "main_turn":
        return ParsedSkip(ScanIssueCode.INTERNAL_REASONING_EXCLUDED, conversation_id)
    call_id = first_string(record.get("requestId"), record.get("turnId"))
    started = parse_timestamp(record.get("startedAt"))
    rows: list[ParsedMessage] = []
    prompt = _new_user_input(request)
    if prompt is not None:
        # What reached the model is the prompt plus whatever the host and our
        # own recall hook injected around it; only the person's words are a
        # user turn. An input that was pure injection is not one.
        envelope = unwrap_host_envelope(strip_injected_memory(prompt))
        if envelope.text is not None:
            rows.append(
                ParsedMessage(
                    conversation_id=conversation_id,
                    message_id=f"{call_id}:user" if call_id else None,
                    role="user",
                    written_by=WrittenBy.PERSON,
                    created_at=started,
                    verbatim=envelope.text,
                    record_type="model_io.user",
                )
            )
    answer = _response_text(response)
    if answer is not None:
        rows.append(
            ParsedMessage(
                conversation_id=conversation_id,
                # Keyed by the call, not the response: a retried call answers
                # the same turn, and a compaction re-read must not file it twice.
                message_id=f"{call_id}:assistant" if call_id else None,
                role="assistant",
                written_by=WrittenBy.AGENT,
                created_at=parse_timestamp(record.get("completedAt")) or started,
                verbatim=answer,
                record_type="model_io.assistant",
            )
        )
    if not rows:
        if prompt is not None:
            # The request closed on a user message that carried no person's
            # words after the injections were stripped.
            return ParsedSkip(ScanIssueCode.NO_TEXT, conversation_id)
        # The tool loop's intermediate calls: requests ending on tool results,
        # responses that only call tools. Their content repeats in later
        # records' histories and in the turn's final answer.
        return ParsedSkip(ScanIssueCode.TOOL_CONTENT_EXCLUDED, conversation_id)
    if len(rows) == 1:
        return rows[0]
    return replace(rows[0], followed_by=tuple(rows[1:]))


def _new_user_input(request: dict[str, JsonValue] | None) -> str | None:
    """The request's closing message, when this call was triggered by a prompt.

    Requests carry the whole history, truncated or delta-shaped as the host
    sees fit, but a prompt always closes its own request — tail and delta
    views keep the end. A tool result or an assistant message closing the
    request marks a continuation, whose user turn was already filed.
    """
    if request is None:
        return None
    messages = request.get("messages")
    if not isinstance(messages, list) or not messages:
        return None
    last = as_record(messages[-1])
    if last is None or first_string(last.get("role")) != "user":
        return None
    return text_content(
        last.get("content"),
        allowed_block_types=frozenset({"text", "input_text"}),
    )


def _response_text(response: dict[str, JsonValue] | None) -> str | None:
    """The response's visible text. Tool-call-only responses carry none."""
    if response is None:
        return None
    text = response.get("text")
    if not isinstance(text, str):
        return None
    return text.strip() or None


def create_zcode_adapter(
    grant: AuthorizedSourceRoot,
    *,
    descriptor: SourceDescriptor = ZCODE_DESCRIPTOR,
    source_version: str | None = None,
) -> JsonlSourceAdapter:
    return JsonlSourceAdapter(
        descriptor=descriptor,
        grant=grant,
        parser=parse_zcode_record,
        source_version=source_version,
        file_predicate=is_zcode_transcript,
    )
