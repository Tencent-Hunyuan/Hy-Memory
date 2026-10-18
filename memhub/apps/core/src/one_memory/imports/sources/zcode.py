"""ZCode source: a card and a recall target, no history import yet.

ZCode keeps per-session model I/O under `~/.zcode/cli/rollout/` as
`model-io-<sessionId>.jsonl`. That stream interleaves request and response
bodies with host bookkeeping, and its record shape has not been mapped yet,
so the import side skips what it sees by name rather than guessing at
semantics. The realtime channel does not wait for that mapping: the recall
install's capture hooks file each turn as Evidence the moment it happens.
"""

from pydantic import JsonValue

from one_memory.imports.access import AuthorizedSourceFile, AuthorizedSourceRoot
from one_memory.imports.adapters import (
    ScanIssueCode,
    SourceCapabilities,
    SourceDescriptor,
    StorageKind,
)
from one_memory.imports.sources.common import (
    JsonlSourceAdapter,
    ParsedSkip,
    SourceRecordContext,
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
    conversation data, which is what a future parser would read.
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
) -> ParsedSkip:
    """Every record is skipped for now, and says so by name.

    The rollout record shape is unmapped; importing a guess would file
    bookkeeping rows as conversation. A skip keeps the sync pipeline
    functional end to end while the mapping is worked out.
    """
    del record, context
    return ParsedSkip(reason_code=ScanIssueCode.UNKNOWN_RECORD)


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
