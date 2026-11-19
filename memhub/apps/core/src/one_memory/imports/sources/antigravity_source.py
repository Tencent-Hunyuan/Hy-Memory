"""Google Antigravity: conversation steps under the host's own brain tree.

Antigravity writes one JSON object per **step** to
``<app_data_dir>/brain/<conversationId>/.system_generated/logs/transcript.jsonl``:

    {"step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
     "status": "DONE", "created_at": "...", "content": "<user_query>...</user_query>"}
    {"step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE",
     "status": "DONE", "content": "the answer", "thinking": "private",
     "tool_calls": [...]}
    {"step_index": 2, "source": "MODEL", "type": "GENERIC", "content": "tool output"}

Only ``USER_INPUT`` and ``PLANNER_RESPONSE`` are visible conversation:
``content`` is the text, ``thinking`` is private chain of thought, and
``GENERIC`` (plus the other bookkeeping types) is tool output, which
live_capture.py promises stays out.

No hook lane: ``api.file_polled`` reports a source as file-backed when its
catalog entry declares ``live_sync``, and a source that live sync already
reads from its own files is deliberately not staged from a hook — the two
lanes give one message different identities and wrote it twice before
(``ebf16b939``). Antigravity's transcript is such a file.

Reference: https://antigravity.google/docs/hooks?tab=ide
"""

import re
from pathlib import PurePosixPath

from pydantic import JsonValue

from one_memory.imports.access import AuthorizedSourceRoot
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
from one_memory.imports.sources.parsing import parse_timestamp

#: The three app data directories Antigravity documents for App / CLI / IDE.
#: Ordered: the first one installed here wins, never mtime.
APP_DATA_DIRS = ("antigravity", "antigravity-cli", "antigravity-ide")

#: ``transcript_full.jsonl`` carries the same steps untruncated. Reading both
#: would import every turn twice, so the scan takes ``transcript.jsonl`` only.
TRANSCRIPT_NAME = "transcript.jsonl"

#: USER_INPUT content arrives wrapped in tags such as ``<user_query>``. Those
#: are the transport envelope, not words the person typed.
_ENVELOPE_TAG = re.compile(r"</?[A-Za-z_][\w.:-]*\s*/?>")
#: Same marker ``live_capture._plain`` filters, kept local so this module does
#: not import live_capture (which now depends on FastAPI and Core state).
_INJECTED_MARKER = "om-derived:"

_VISIBLE_TYPES = {
    "USER_INPUT": ("user", WrittenBy.PERSON),
    "PLANNER_RESPONSE": ("assistant", WrittenBy.AGENT),
}

ANTIGRAVITY_DESCRIPTOR = SourceDescriptor(
    source_id="antigravity",
    display_name="Google Antigravity",
    storage_kinds=frozenset({StorageKind.JSONL}),
    capabilities=SourceCapabilities(
        can_append=True,
        can_observe_edits=False,
        can_observe_deletes=False,
        can_reconcile_complete_set=False,
    ),
)


def create_antigravity_adapter(grant: AuthorizedSourceRoot) -> JsonlSourceAdapter:
    return JsonlSourceAdapter(
        descriptor=ANTIGRAVITY_DESCRIPTOR,
        grant=grant,
        parser=parse_antigravity_record,
    )


def visible_text(content: str) -> str | None:
    """The person's or the model's words, without envelope or injected preface."""
    stripped = _ENVELOPE_TAG.sub("", content)
    kept = [
        line
        for line in stripped.splitlines()
        if line.strip() and _INJECTED_MARKER not in line
    ]
    return "\n".join(kept).strip() or None


def parse_antigravity_record(
    record: dict[str, JsonValue],
    context: SourceRecordContext,
) -> ParsedMessage | ParsedSkip:
    """One JSONL step -> one visible turn, or the reason it was left out."""
    path = PurePosixPath(context.relative_path)
    if path.name != TRANSCRIPT_NAME or ".system_generated" not in path.parts:
        return ParsedSkip(reason_code=ScanIssueCode.UNKNOWN_RECORD)
    # <conversationId>/.system_generated/logs/transcript.jsonl, relative to the
    # granted brain root. The directory name is the host's conversation id.
    conversation_id = path.parts[0] if path.parts else ""
    if not conversation_id:
        return ParsedSkip(reason_code=ScanIssueCode.UNKNOWN_RECORD)

    kind = record.get("type")
    if kind not in _VISIBLE_TYPES:
        # GENERIC (tool output) and the bookkeeping types are not conversation.
        return ParsedSkip(reason_code=ScanIssueCode.UNKNOWN_RECORD)
    if record.get("status") not in (None, "DONE"):
        # A step still being written would be imported half-formed, then
        # re-imported as a revision; leaving it out is what the cursor expects.
        return ParsedSkip(reason_code=ScanIssueCode.UNKNOWN_RECORD)
    clipped = record.get("truncated_fields")
    if isinstance(clipped, (list, tuple)) and "content" in clipped:
        return ParsedSkip(reason_code=ScanIssueCode.NO_TEXT, conversation_id=conversation_id)
    content = record.get("content")
    if not isinstance(content, str):
        return ParsedSkip(reason_code=ScanIssueCode.NO_TEXT, conversation_id=conversation_id)
    text = visible_text(content)
    if text is None:
        return ParsedSkip(reason_code=ScanIssueCode.NO_TEXT, conversation_id=conversation_id)

    step = record.get("step_index")
    message_id = (
        f"step-{step}"
        if isinstance(step, int) and not isinstance(step, bool)
        else f"line-{context.ordinal}"
    )
    role, written_by = _VISIBLE_TYPES[kind]
    return ParsedMessage(
        conversation_id=conversation_id,
        message_id=message_id,
        role=role,
        written_by=written_by,
        created_at=parse_timestamp(record.get("created_at")),
        verbatim=text,
        record_type="step",
    )
