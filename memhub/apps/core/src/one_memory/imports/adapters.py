from collections.abc import AsyncIterator
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from one_memory.imports.models import EvidenceInput


class StorageKind(StrEnum):
    JSONL = "jsonl"
    SQLITE = "sqlite"
    ZSTD = "zstd"
    HOOK = "hook"
    API = "api"


class ScanIssueCode(StrEnum):
    INVALID_JSON = "invalid_json"
    RECORD_TOO_LARGE = "record_too_large"
    UNKNOWN_RECORD = "unknown_record"
    NO_TEXT = "no_text"
    TOOL_CONTENT_EXCLUDED = "tool_content_excluded"
    INTERNAL_REASONING_EXCLUDED = "internal_reasoning_excluded"
    #: A turn arrived wearing the user's role but was written by a machine —
    #: a scheduled job, a title generator, a host's own bookkeeping. Kept
    #: distinct from internal reasoning because the reason a user sees for a
    #: skipped turn should say what actually happened.
    NOT_WRITTEN_BY_PERSON = "not_written_by_person"
    #: A sub agent's transcript, skipped because the deployment asked not to
    #: import it. Named for the reason, so a reader can tell a setting from
    #: a record the parser could not use.
    SUBAGENT_TRACE_DISABLED = "subagent_trace_disabled"
    SOURCE_BUSY = "source_busy"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    BUDGET_EXCEEDED = "budget_exceeded"
    #: The file is bigger than a per-file ceiling: either the import's own
    #: ceiling or the access gate's per-root `max_file_bytes`. Both are skipped
    #: with this reason rather than read, because one oversized history file
    #: can hold the import open long enough to take the process with it, and
    #: the user-facing cause is the same size limit.
    FILE_TOO_LARGE = "file_too_large"
    HISTORY_WINDOW_EXCLUDED = "history_window_excluded"


class ScanPhase(StrEnum):
    DISCOVER = "discover"
    READ = "read"
    EMIT = "emit"
    DONE = "done"


class SyncCompleteness(StrEnum):
    """How much of the source's own changes a live connection can follow.

    A source we can only read forward from will never tell us that something
    was deleted upstream, so a memory drawn from it can outlive the sentence it
    came from. That is a different promise from one we can reconcile against,
    and the user has to be told which one they are getting.
    """

    FULL_DYNAMIC = "full_dynamic"
    ADDITIONS_ONLY = "additions_only"
    ONE_TIME_SNAPSHOT = "one_time_snapshot"


class SourceCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    can_append: bool
    can_observe_edits: bool
    can_observe_deletes: bool
    can_reconcile_complete_set: bool

    @property
    def sync_completeness(self) -> SyncCompleteness:
        """Derived, never stored: a label kept by hand drifts from the booleans.

        Deletions can be followed either by observing the events directly or by
        reconciling against a known-complete set. With neither, the honest claim
        is additions only.
        """
        if not any(
            (
                self.can_append,
                self.can_observe_edits,
                self.can_observe_deletes,
                self.can_reconcile_complete_set,
            )
        ):
            return SyncCompleteness.ONE_TIME_SNAPSHOT
        if self.can_observe_deletes or self.can_reconcile_complete_set:
            return SyncCompleteness.FULL_DYNAMIC
        return SyncCompleteness.ADDITIONS_ONLY


class SourceDescriptor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1, max_length=512)
    display_name: str = Field(min_length=1, max_length=256)
    storage_kinds: frozenset[StorageKind] = Field(min_length=1)
    capabilities: SourceCapabilities


class SourceCursor(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    file_key: str = Field(min_length=64, max_length=64)
    file_identity: str = Field(min_length=1, max_length=256)
    checkpoint: str = Field(min_length=1, max_length=8192)
    checkpoint_anchor: str | None = Field(
        default=None,
        min_length=64,
        max_length=64,
    )
    observed_size: int = Field(ge=0)
    observed_modified_ns: int = Field(ge=0)
    #: The checkpoint reached the end of the file as observed. Only then may an
    #: unchanged size and mtime mean there is nothing left to read.
    read_to_end: bool = False


class ScanOptions(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    cursors: dict[str, SourceCursor] = Field(default_factory=dict)
    max_messages: int | None = Field(default=None, ge=1, le=1_000_000)
    max_scan_targets: int | None = Field(default=None, ge=1, le=100_000)
    #: The import's per-file ceiling. `None` falls back to
    #: `DEFAULT_IMPORT_MAX_FILE_BYTES` at read time so an unset option still
    #: protects the process; set it explicitly to raise or remove the ceiling.
    max_source_file_bytes: int | None = Field(default=None, ge=1)
    immutable: bool = False
    reuse_listing: bool = False


class ScannedEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["evidence"] = "evidence"
    evidence: EvidenceInput
    cursor: SourceCursor


class ScanFailure(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["error"] = "error"
    cursor: SourceCursor | None = None
    conversation_id: str | None = Field(default=None, max_length=512)
    reason_code: ScanIssueCode


class ScanSkip(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["skip"] = "skip"
    cursor: SourceCursor
    conversation_id: str | None = Field(default=None, max_length=512)
    reason_code: ScanIssueCode
    report_issue: bool = False


class ScanProgressEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["progress"] = "progress"
    phase: ScanPhase
    discovered_increment: int = Field(default=0, ge=0)


class ScanCursorTouch(BaseModel):
    """A file was read to its end and yielded nothing; only its cursor moves."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["cursor"] = "cursor"
    cursor: SourceCursor


type ScanEvent = ScannedEvidence | ScanFailure | ScanSkip | ScanProgressEvent | ScanCursorTouch


class SourceAdapter(Protocol):
    @property
    def descriptor(self) -> SourceDescriptor: ...

    async def detect(self) -> bool: ...

    def scan(self, options: ScanOptions) -> AsyncIterator[ScanEvent]: ...
