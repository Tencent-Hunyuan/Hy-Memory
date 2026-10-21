import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
from collections.abc import AsyncIterator, Callable, Generator, Iterator
from dataclasses import dataclass, replace
from datetime import datetime
from functools import partial
from itertools import zip_longest
from pathlib import Path

from pydantic import JsonValue

from one_memory.diagnostic_log import diagnostic_event
from one_memory.imports.access import (
    AuthorizedSourceFile,
    AuthorizedSourceRoot,
    SourceAccessError,
    SourceAccessErrorCode,
    opaque_workspace_id,
    safe_project_label,
)
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
    SourceCursor,
    SourceDescriptor,
)
from one_memory.imports.async_bridge import iterate_in_worker
from one_memory.imports.discovery import (
    SourceFileDiscoveryEvent,
    SourceFileDiscoveryFailure,
    discover_source_files,
    discovered_off_loop,
)
from one_memory.imports.models import (
    MAX_VERBATIM_CHARACTERS,
    EvidenceContentKind,
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
)
from one_memory.imports.readers import (
    DEFAULT_IMPORT_MAX_FILE_BYTES,
    MAX_JSONL_RECORD_BYTES,
    RawReadEvent,
    RawReadFailure,
    RawReadFailureCode,
    RawRecord,
    read_jsonl,
    read_zstd_jsonl,
    verify_jsonl_checkpoint,
)
from one_memory.imports.sources.parsing import parse_timestamp

_log = logging.getLogger(__name__)


def _clamp_verbatim(text: str) -> str:
    """Keep one pathological record from violating EvidenceInput's limit.

    A single pasted dump larger than MAX_VERBATIM_CHARACTERS used to fail
    validation, retry, and then wedge the whole history import on the same
    record. Keep the head and make the truncation explicit and actionable.
    """
    if len(text) <= MAX_VERBATIM_CHARACTERS:
        return text
    marker = f"\n…[truncated: original {len(text)} chars]"
    keep = max(0, MAX_VERBATIM_CHARACTERS - len(marker))
    return text[:keep] + marker

#: At most one read heartbeat per second. Faster would be more writes than the
#: counts are worth; slower leaves the job's visible state stale.
_READ_PROGRESS_INTERVAL_SECONDS = 1.0
#: Heartbeats only land on a write-batch boundary, so a heartbeat can never
#: split a batch and change what a failed commit rolls back. Kept equal to the
#: runner's batch size; importing it here would make sources depend on the
#: runner, and the guard test asserts the two stay in step.
_HEARTBEAT_BATCH_BOUNDARY = 10


def rotate_history_files(
    files: list[AuthorizedSourceFile], cursors: dict[str, SourceCursor]
) -> list[AuthorizedSourceFile]:
    """Alternate cohorts, finishing small remaining files before giant ones."""
    started = sorted(
        (item for item in files if item.safe_id in cursors),
        key=lambda item: (
            _planned_read_bytes(item, cursors[item.safe_id].checkpoint),
            item.safe_id,
        ),
    )
    untouched = sorted(
        (item for item in files if item.safe_id not in cursors),
        key=lambda item: (item.size, item.safe_id),
    )
    return [item for pair in zip_longest(untouched, started) for item in pair if item is not None]


@dataclass(frozen=True)
class SourceRecordContext:
    file_key: str
    relative_path: str
    ordinal: str
    modified_ns: int = 0
    #: The file's opening record, when the source declares that it writes one.
    #: Some hosts state the session's working directory once at the top and never
    #: repeat it, so a per-record parser cannot see it at all.
    header: dict[str, JsonValue] | None = None


@dataclass(frozen=True)
class ParsedMessage:
    conversation_id: str
    role: str
    written_by: WrittenBy
    verbatim: str
    #: What the record held before this build's cleaning ran. Identity hashes
    #: this, so a cleaning change cannot re-key rows that are already stored.
    raw_verbatim: str | None = None
    message_id: str | None = None
    created_at: datetime | None = None
    workspace: str | None = None
    project: str | None = None
    record_type: str | None = None
    content_kind: EvidenceContentKind = EvidenceContentKind.RAW_CONVERSATION
    origin_metadata: OriginMetadata | None = None
    #: Further rows the same source record holds, in the order they come after
    #: this one: a reply that says something and then calls a tool is two rows.
    followed_by: "tuple[ParsedMessage, ...]" = ()

    def rows(self) -> "tuple[ParsedMessage, ...]":
        return (self, *self.followed_by)


def followed_by_tool(parsed: ParsedMessage, tool_text: str | None) -> ParsedMessage:
    """The text row of a record that also called tools, with those calls as the next row."""
    if tool_text is None:
        return parsed
    tool = ParsedMessage(
        conversation_id=parsed.conversation_id,
        message_id=f"{parsed.message_id}:tool" if parsed.message_id else None,
        role="tool",
        written_by=WrittenBy.TOOL,
        created_at=parsed.created_at,
        verbatim=tool_text,
        raw_verbatim=tool_text,
        workspace=parsed.workspace,
        project=parsed.project,
        record_type=f"{parsed.record_type or 'message'}.tool",
    )
    return replace(parsed, followed_by=(*parsed.followed_by, tool))


@dataclass(frozen=True)
class ParsedSkip:
    reason_code: ScanIssueCode
    conversation_id: str | None = None
    report_issue: bool = False


type SourceRecordParser = Callable[
    [dict[str, JsonValue], SourceRecordContext],
    ParsedMessage | ParsedSkip,
]
type JsonlReader = Callable[..., Generator[RawReadEvent, None, None]]


@dataclass(frozen=True)
class SourceInstallState:
    """The two-level discovery answer, kept from collapsing into one boolean.

    `installed` is a directory fact: the agreed root exists, so the host is on
    this machine. `session_count` is a content fact: how many conversations the
    importer could actually read. A freshly installed host answers
    (True, 0) — installed, with nothing to import yet — which is a different
    state from (False, 0), where there is nothing to import because the host
    is not there.

    `detected` stays for the existing one-level callers: True whenever the root
    exists, whether or not it holds sessions yet.
    """

    installed: bool
    session_count: int
    detected: bool


class JsonlSourceAdapter:
    def __init__(
        self,
        *,
        descriptor: SourceDescriptor,
        grant: AuthorizedSourceRoot,
        parser: SourceRecordParser,
        source_version: str | None = None,
        file_predicate: Callable[[AuthorizedSourceFile], bool] | None = None,
        suffixes: frozenset[str] = frozenset({".jsonl"}),
        header_selector: Callable[[dict[str, JsonValue]], bool] | None = None,
    ) -> None:
        if descriptor.source_id != grant.source_id:
            raise ValueError("Descriptor and authorized root source IDs must match")
        self._descriptor = descriptor
        self.grant = grant
        self.parser = parser
        self.source_version = source_version
        self.file_predicate = file_predicate or (lambda _: True)
        self.suffixes = suffixes
        self.header_selector = header_selector
        self._header_cache: dict[str, tuple[str, dict[str, JsonValue] | None]] = {}
        self._history_listing_cache: SourceListing | None = None

    @property
    def descriptor(self) -> SourceDescriptor:
        return self._descriptor

    async def detect(self) -> bool:
        state = await self.install_state()
        return state.detected

    async def install_state(self) -> SourceInstallState:
        """Two-level discovery: the root's existence and its sessions, separately.

        A host the user just installed has a root directory and no transcripts
        yet. Returning False there reported "not installed", so the source card
        offered to install something already on disk and never explained the
        empty import. Installed-with-zero-sessions is now its own answer.

        Detection stays metadata-only: the walk lstats candidates and never
        opens a transcript, which is what makes it cheap enough to re-probe.
        Nothing about a probe is cached, so a host installed after the first
        probe is discovered by the next one.
        """
        installed = await asyncio.to_thread(self.grant.root.is_dir)
        if not installed:
            return SourceInstallState(installed=False, session_count=0, detected=False)
        sessions = await asyncio.to_thread(self._count_sessions)
        return SourceInstallState(installed=True, session_count=sessions, detected=True)

    async def count_sessions(self, *, limit: int | None = None) -> int:
        """Count metadata only, stopping early when a policy needs a threshold."""
        return await asyncio.to_thread(self._count_sessions, limit=limit)

    def _count_sessions(self, budget: int = 100_000, *, limit: int | None = None) -> int:
        counted = 0
        for item in discover_source_files(
            self.grant,
            suffixes=self.suffixes,
            max_targets=budget,
        ):
            if isinstance(item, AuthorizedSourceFile) and self.file_predicate(item):
                counted += 1
                if limit is not None and counted >= limit:
                    break
        return counted

    async def has_changes(self, options: ScanOptions) -> bool:
        """Check file metadata without opening conversation contents."""
        listing = await asyncio.to_thread(
            list_changed_files,
            self.grant,
            suffixes=self.suffixes,
            max_targets=options.max_scan_targets or 100_000,
            predicate=self.file_predicate,
            cursors=options.cursors,
        )
        return bool(listing.changed or listing.failures)

    async def live_baseline(self) -> dict[str, SourceCursor]:
        """Capture complete EOFs so realtime polling never backfills history."""
        files = await asyncio.to_thread(
            list,
            discover_source_files(
                self.grant,
                suffixes=self.suffixes,
                max_targets=100_000,
            ),
        )
        return await asyncio.to_thread(self._live_baseline_sync, files)

    def _live_baseline_sync(self, files: list[SourceFileDiscoveryEvent]) -> dict[str, SourceCursor]:
        cursors: dict[str, SourceCursor] = {}
        for item in files:
            if not isinstance(item, AuthorizedSourceFile) or not self.file_predicate(item):
                continue
            if item.relative_path.name.endswith(".zstd"):
                continue
            checkpoint, anchor = jsonl_complete_tail(item)
            cursors[item.safe_id] = source_cursor(
                item,
                checkpoint,
                checkpoint_anchor=anchor,
            ).model_copy(update={"read_to_end": checkpoint == f"byte:{item.size}"})
        return cursors

    async def scan(self, options: ScanOptions) -> AsyncIterator[ScanEvent]:
        if options.immutable:
            async for event in self._scan_frozen(options):
                yield event
            return
        listing = await asyncio.to_thread(
            self._history_listing if options.reuse_listing else self._fresh_listing,
            options,
        )
        for failure in listing.failures:
            yield ScanFailure(reason_code=discovery_failure_code(failure))
        yield ScanProgressEvent(phase=ScanPhase.DISCOVER, discovered_increment=listing.total)
        async for event in self.read_listed(listing.changed, options):
            yield event
        yield ScanProgressEvent(phase=ScanPhase.DONE)

    def _history_listing(self, options: ScanOptions) -> "SourceListing":
        """Reuse one authorized listing while a bounded history job advances.

        A runner slice commits its cursor before returning, so filtering the
        cached metadata against those cursors is sufficient to resume. Once
        every cached file reached EOF, refresh once to catch files created
        during the import before reporting completion.
        """
        cached = self._history_listing_cache
        if cached is not None:
            remaining = [item for item in cached.changed if not is_unchanged(item, options.cursors)]
            if remaining:
                # A partially read large file goes behind files that have not
                # had a slice yet. Each file still resumes from its committed
                # byte/record cursor; only the next-file choice rotates.
                remaining = rotate_history_files(remaining, options.cursors)
                return SourceListing(total=cached.total, changed=remaining, failures=[])
            self._history_listing_cache = None
        listing = self._fresh_listing(options)
        if listing.changed and not listing.failures:
            self._history_listing_cache = listing
        return listing

    def _fresh_listing(self, options: ScanOptions) -> "SourceListing":
        return list_changed_files(
            self.grant,
            suffixes=self.suffixes,
            max_targets=options.max_scan_targets or 100_000,
            predicate=self.file_predicate,
            cursors=options.cursors,
        )

    async def read_listed(
        self, files: list[AuthorizedSourceFile], options: ScanOptions, *, announce: bool = True
    ) -> AsyncIterator[ScanEvent]:
        """Read each changed file forward from its cursor, in the order given."""
        if files and announce:
            yield ScanProgressEvent(phase=ScanPhase.READ)
        # A long file used to emit no progress at all between files, so the job's
        # visible counts could stand still for seconds. Emit at most one real
        # heartbeat per second while reading; the increment is what has actually
        # been read, never an estimated percentage of an unknown total.
        progress_due = 0.0
        emitted = 0
        for index, listed in enumerate(files, start=1):
            last: SourceCursor | None = None
            try:
                source_file, checkpoint, header = await asyncio.to_thread(
                    self._prepare, listed, options
                )
                _log.debug(
                    "import_file_read source=%s file=%d/%d file_bytes=%d planned_bytes=%d",
                    self.descriptor.source_id,
                    index,
                    len(files),
                    source_file.size,
                    _planned_read_bytes(source_file, checkpoint),
                )
                # One pass reads at most this many bytes of one file. A large
                # file is not skipped: it is read in bounded slices, and the
                # cursor the run ends on becomes the next run's checkpoint.
                # Skipping was tried first and measured badly -- at 32MB it
                # dropped 30 of 474 codex files, which is 6% of the files but
                # 85% of the bytes, so the guard was quietly throwing away
                # most of the history it existed to protect.
                budget = (
                    options.max_source_file_bytes
                    if options.max_source_file_bytes is not None
                    else DEFAULT_IMPORT_MAX_FILE_BYTES
                )
                if source_file.size > budget:
                    _log.debug(
                        "import_file_budgeted source=%s file=%d/%d file_bytes=%d budget=%d path=%s",
                        self.descriptor.source_id,
                        index,
                        len(files),
                        source_file.size,
                        budget,
                        source_file.relative_path,
                    )
                reader: JsonlReader = (
                    read_zstd_jsonl
                    if source_file.relative_path.name.endswith(".zstd")
                    else read_jsonl
                )
                before = _start_cursor(source_file, options, checkpoint)
                async for raw in iterate_in_worker(
                    partial(reader, source_file, checkpoint=checkpoint, max_bytes=budget),
                    scope_key=self.descriptor.source_id,
                ):
                    events = await asyncio.to_thread(
                        self._events,
                        source_file,
                        raw,
                        header,
                        before=last or before,
                    )
                    last = events[-1].cursor
                    for event in events:
                        yield event
                    if isinstance(events[-1], ScannedEvidence):
                        emitted += 1
                        if options.max_messages is not None and emitted >= options.max_messages:
                            return
                        if emitted % 100 == 0:
                            await asyncio.sleep(0)
                        # Only between batches: a heartbeat inside an open
                        # write batch would split it, changing how much one
                        # transaction commits and therefore what a failure
                        # rolls back.
                        now = asyncio.get_running_loop().time()
                        if now >= progress_due and emitted % _HEARTBEAT_BATCH_BOUNDARY == 0:
                            progress_due = now + _READ_PROGRESS_INTERVAL_SECONDS
                            yield ScanProgressEvent(phase=ScanPhase.EMIT, discovered_increment=0)
            except SourceAccessError as error:
                self._history_listing_cache = None
                if error.code in STOP_SCAN_ACCESS_CODES:
                    raise
                # A file that cannot be opened as listed is a per-file failure
                # the user must be able to see. Dropping it silently made a job
                # report success while part of the source was never read.
                yield ScanFailure(reason_code=_scan_failure_code(error.code))
                continue
            yield ScanCursorTouch(
                cursor=touched_cursor(
                    listed,
                    options,
                    last=last,
                    empty_checkpoint=(
                        "record:0" if listed.relative_path.name.endswith(".zstd") else "byte:0"
                    ),
                )
            )

    def _prepare(
        self, listed: AuthorizedSourceFile, options: ScanOptions
    ) -> tuple[AuthorizedSourceFile, str | None, dict[str, JsonValue] | None]:
        source_file = listed.admit()
        checkpoint = resume_checkpoint(source_file, options)
        reader: JsonlReader = (
            read_zstd_jsonl if source_file.relative_path.name.endswith(".zstd") else read_jsonl
        )
        header = None if self.header_selector is None else self._header_for(source_file, reader)
        return source_file, checkpoint, header

    def _header_for(
        self,
        source_file: AuthorizedSourceFile,
        reader: JsonlReader,
    ) -> dict[str, JsonValue] | None:
        selector = self.header_selector
        if selector is None:
            return None
        cached = self._header_cache.get(source_file.safe_id)
        if cached is not None and cached[0] == source_file.file_identity:
            return cached[1]
        header = _first_record_if_header(reader, source_file, selector)
        self._header_cache[source_file.safe_id] = (source_file.file_identity, header)
        if len(self._header_cache) > 4096:
            self._header_cache.pop(next(iter(self._header_cache)))
        return header

    def _events(
        self,
        source_file: AuthorizedSourceFile,
        raw: RawReadEvent,
        header: dict[str, JsonValue] | None,
        *,
        before: SourceCursor,
    ) -> list[ScannedEvidence | ScanFailure | ScanSkip]:
        """One record's events. All but the last carry the cursor from before the record,
        so a scan stopped between them reads the record again; ids keep that from
        duplicating anything."""
        cursor = source_cursor(source_file, raw.checkpoint, checkpoint_anchor=raw.checkpoint_anchor)
        if isinstance(raw, RawReadFailure):
            return [ScanFailure(cursor=cursor, reason_code=_map_read_failure(raw.reason_code))]
        context = SourceRecordContext(
            file_key=source_file.safe_id,
            relative_path=source_file.relative_path.as_posix(),
            ordinal=f"{source_file.safe_id}:{raw.checkpoint}",
            modified_ns=source_file.modified_ns,
            header=header,
        )
        parsed = self.parser(raw.value, context)
        if isinstance(parsed, ParsedSkip):
            return [
                ScanSkip(
                    cursor=cursor,
                    conversation_id=parsed.conversation_id,
                    reason_code=parsed.reason_code,
                    report_issue=parsed.report_issue,
                )
            ]
        rows = parsed.rows()
        return [
            ScannedEvidence(
                cursor=cursor if index == len(rows) - 1 else before,
                evidence=to_evidence(
                    source_id=self.descriptor.source_id,
                    source_file=source_file,
                    parsed=row,
                    ordinal=row_ordinal(context.ordinal, index),
                    source_version=self.source_version,
                ),
            )
            for index, row in enumerate(rows)
        ]

    async def _scan_frozen(self, options: ScanOptions) -> AsyncIterator[ScanEvent]:
        """A caller-owned immutable copy: the snapshot store's path, not the import path."""
        max_targets = options.max_scan_targets or 100_000
        emitted = 0
        if self.descriptor.source_id in {
            "workbuddy",
            "claude-code",
            "codex",
            "cursor",
        }:
            files = iterate_in_worker(
                partial(
                    _recent_history_files,
                    self.grant,
                    suffixes=self.suffixes,
                    max_targets=max_targets,
                ),
                scope_key=self.descriptor.source_id,
            )
        else:
            files = discovered_off_loop(
                self.grant,
                suffixes=self.suffixes,
                max_targets=max_targets,
            )
        async for discovered in files:
            yield ScanProgressEvent(
                phase=ScanPhase.DISCOVER,
                discovered_increment=1,
            )
            if isinstance(discovered, SourceFileDiscoveryFailure):
                yield ScanFailure(reason_code=discovery_failure_code(discovered))
                continue
            source_file = discovered
            if not self.file_predicate(source_file):
                continue
            checkpoint = resume_checkpoint(source_file, options)
            reader: JsonlReader = (
                read_zstd_jsonl if source_file.relative_path.name.endswith(".zstd") else read_jsonl
            )
            reader = partial(reader, immutable=options.immutable)
            # Read deliberately from the top rather than from the checkpoint: a
            # resumed scan starts past the header, and the records after it need
            # it just as much as the first batch did.
            header = await _read_header(reader, source_file, self.header_selector)
            last: SourceCursor = _start_cursor(source_file, options, checkpoint)
            async for raw in iterate_in_worker(
                partial(reader, source_file, checkpoint=checkpoint),
                scope_key=self.descriptor.source_id,
            ):
                events = self._events(source_file, raw, header, before=last)
                last = events[-1].cursor or last
                for event in events:
                    yield event
                if not isinstance(events[-1], ScannedEvidence):
                    continue
                emitted += 1
                if options.max_messages is not None and emitted >= options.max_messages:
                    return
                if emitted % 100 == 0:
                    await asyncio.sleep(0)
        yield ScanProgressEvent(phase=ScanPhase.DONE)


#: A file that cannot be opened as listed is that file's problem; these are not.
#: They mean the grant or the folder itself changed, so the whole scan stops.
STOP_SCAN_ACCESS_CODES = frozenset(
    {
        SourceAccessErrorCode.GRANT_REQUIRED,
        SourceAccessErrorCode.SOURCE_PERMISSION_CHANGED,
        SourceAccessErrorCode.SOURCE_IDENTITY_CHANGED,
    }
)


@dataclass(frozen=True)
class SourceListing:
    total: int
    #: Files whose inode, size or mtime differ from their cursor, newest first.
    changed: list[AuthorizedSourceFile]
    failures: list[SourceFileDiscoveryFailure]


def list_changed_files(
    grant: AuthorizedSourceRoot,
    *,
    suffixes: frozenset[str],
    max_targets: int,
    predicate: Callable[[AuthorizedSourceFile], bool],
    cursors: dict[str, SourceCursor],
) -> SourceListing:
    total = 0
    changed: list[AuthorizedSourceFile] = []
    failures: list[SourceFileDiscoveryFailure] = []
    for item in discover_source_files(grant, suffixes=suffixes, max_targets=max_targets):
        if isinstance(item, SourceFileDiscoveryFailure):
            failures.append(item)
            continue
        if not predicate(item):
            continue
        total += 1
        if not is_unchanged(item, cursors):
            changed.append(item)
    return SourceListing(total=total, changed=newest_first(changed), failures=failures)


def newest_first(files: list[AuthorizedSourceFile]) -> list[AuthorizedSourceFile]:
    return sorted(files, key=lambda item: (-item.modified_ns, item.relative_path.as_posix()))


def is_unchanged(source_file: AuthorizedSourceFile, cursors: dict[str, SourceCursor]) -> bool:
    previous = cursors.get(source_file.safe_id)
    return (
        previous is not None
        and previous.read_to_end
        and previous.file_identity == source_file.file_identity
        and previous.observed_size == source_file.size
        and previous.observed_modified_ns == source_file.modified_ns
        # Chmod alone leaves size, mtime and inode untouched, so a file that
        # just became unreadable would otherwise be skipped as unchanged and
        # never reported. Readability is checked from the metadata we already
        # have, with no file body read.
        and _is_readable(source_file.path)
    )


def _is_readable(path: Path) -> bool:
    """Cheap readability check on an already-listed path; never reads contents."""
    try:
        return os.access(path, os.R_OK)
    except OSError:
        return False


def _start_cursor(
    source_file: AuthorizedSourceFile, options: ScanOptions, checkpoint: str | None
) -> SourceCursor:
    """Where this read began: the cursor it resumed from, or the top of the file."""
    previous = options.cursors.get(source_file.safe_id)
    if checkpoint is not None and previous is not None and previous.checkpoint == checkpoint:
        return previous
    empty = "record:0" if source_file.relative_path.name.endswith(".zstd") else "byte:0"
    return source_cursor(source_file, empty)


def touched_cursor(
    source_file: AuthorizedSourceFile,
    options: ScanOptions,
    *,
    last: SourceCursor | None,
    empty_checkpoint: str,
) -> SourceCursor:
    """Mark a file read to its end, at the last record read or the old checkpoint.

    Sent only after the reader finished the file. A scan stopped partway never
    sends it, so the next scan cannot mistake the unread rest for unchanged.
    """
    if last is not None:
        return last.model_copy(update={"read_to_end": True})
    previous = options.cursors.get(source_file.safe_id)
    keep = (
        previous is not None
        and previous.file_identity == source_file.file_identity
        and previous.observed_size <= source_file.size
    )
    # A kept cursor means the next scan resumes exactly where this one ended:
    # nothing rewound, so there is nothing to report. Only the reset path
    # (identity changed, file shrank, or no prior cursor) is a real rewind.
    if previous is not None and not keep:
        _emit_cursor_rewound(
            source_file,
            previous=previous,
            new_checkpoint=empty_checkpoint,
            branch="touched_cursor_no_records",
            previous_checkpoint=previous.checkpoint,
            read_to_end=previous.read_to_end,
        )
    return source_cursor(
        source_file,
        previous.checkpoint if keep and previous is not None else empty_checkpoint,
        checkpoint_anchor=previous.checkpoint_anchor if keep and previous is not None else None,
    ).model_copy(update={"read_to_end": True})


def _emit_cursor_rewound(
    source_file: AuthorizedSourceFile,
    *,
    previous: SourceCursor,
    new_checkpoint: str,
    branch: str,
    previous_checkpoint: str,
    read_to_end: bool,
) -> None:
    """Report a live-cursor rewind with numbers only, never file contents."""
    try:
        new_offset = int(new_checkpoint.removeprefix("byte:"))
    except ValueError:
        new_offset = -1
    try:
        old_offset = int(previous_checkpoint.removeprefix("byte:"))
    except ValueError:
        old_offset = -1
    if new_offset >= old_offset:
        return
    inode = 0
    with contextlib.suppress(OSError):
        inode = source_file.path.stat().st_ino
    diagnostic_event(
        level="warn",
        component="import.live_sync",
        event="live_sync.cursor_rewound",
        outcome="rewound",
        error_code="live_cursor_rewound",
        details={
            "cursor_rewind_branch": branch,
            "cursor_old": old_offset,
            "cursor_new": new_offset,
            "cursor_read_to_end": read_to_end,
            "file_size": source_file.size,
            "file_mtime_ns": source_file.modified_ns,
            "file_inode": inode,
        },
    )


def _recent_history_files(
    grant: AuthorizedSourceRoot,
    *,
    suffixes: frozenset[str],
    max_targets: int,
) -> Iterator[SourceFileDiscoveryEvent]:
    # Newest conversation first. A file's last record time wins; mtime is only
    # the fallback when that record has no timestamp.
    files = []
    for item in discover_source_files(grant, suffixes=suffixes, max_targets=max_targets):
        if isinstance(item, SourceFileDiscoveryFailure):
            yield item
        else:
            files.append(item)
    yield from sorted(
        files,
        key=lambda item: (
            -_conversation_recency_ns(item),
            -item.modified_ns,
            item.relative_path.as_posix(),
        ),
    )


def _conversation_recency_ns(item: AuthorizedSourceFile) -> int:
    stamped = _tail_timestamp_ns(item.path)
    return item.modified_ns if stamped is None else stamped


def _tail_timestamp_ns(path: Path) -> int | None:
    """Last complete record's time, from the tail only. Missing time stays None."""
    if path.suffix.lower() not in {".jsonl", ".zstd"}:
        return None
    try:
        size = path.stat().st_size
        if size <= 0:
            return None
        with path.open("rb") as handle:
            handle.seek(max(0, size - 8192))
            chunk = handle.read()
    except OSError:
        return None
    lines = chunk.splitlines()
    if size > 8192 and lines:
        lines = lines[1:]
    for raw in reversed(lines):
        text = raw.strip()
        if not text:
            continue
        try:
            record = json.loads(text)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(record, dict):
            continue
        stamp = _record_timestamp(record)
        if stamp is not None:
            return int(stamp.timestamp() * 1_000_000_000)
    return None


def _record_timestamp(record: dict[str, JsonValue]) -> datetime | None:
    candidates: list[JsonValue] = []
    for key in ("timestamp", "createdAt", "created_at", "time"):
        if key in record:
            candidates.append(record[key])
    message = record.get("message")
    if isinstance(message, dict) and "timestamp" in message:
        candidates.append(message["timestamp"])
    payload = record.get("payload")
    if isinstance(payload, dict) and "timestamp" in payload:
        candidates.append(payload["timestamp"])
    for value in candidates:
        stamp = parse_timestamp(value)
        if stamp is not None:
            return stamp
    return None


async def _read_header(
    reader: JsonlReader,
    source_file: AuthorizedSourceFile,
    selector: Callable[[dict[str, JsonValue]], bool] | None,
) -> dict[str, JsonValue] | None:
    if selector is None:
        return None
    return await asyncio.to_thread(_first_record_if_header, reader, source_file, selector)


def _first_record_if_header(
    reader: JsonlReader,
    source_file: AuthorizedSourceFile,
    selector: Callable[[dict[str, JsonValue]], bool],
) -> dict[str, JsonValue] | None:
    """Read one record, no more: a header we cannot find must not cost a full file read."""
    records = reader(source_file, checkpoint=None)
    try:
        first = next(records, None)
    finally:
        records.close()
    if not isinstance(first, RawRecord) or not selector(first.value):
        return None
    return first.value


def resume_checkpoint(
    source_file: AuthorizedSourceFile,
    options: ScanOptions,
) -> str | None:
    """Where a scan of this file should start, or None for the beginning.

    `None` means "read from zero", which for a large session file costs a whole
    re-read, so every branch here is deliberate.
    """
    previous = options.cursors.get(source_file.safe_id)
    if previous is None:
        return None
    if previous.file_identity != source_file.file_identity:
        return None
    if source_file.size < previous.observed_size:
        return None
    if (
        previous.checkpoint.startswith("byte:")
        and previous.checkpoint_anchor
        and not verify_jsonl_checkpoint(
            _anchor_view(source_file, previous),
            checkpoint=previous.checkpoint,
            anchor=previous.checkpoint_anchor,
        )
    ):
        # A host may rewrite the tail line in place with the same byte length
        # (same size, same identity, same mtime), which changes the anchored
        # line's hash without changing anything the metadata gate can see. The
        # stored offset is still inside the file, so re-reading from zero would
        # redo the whole session for one changed line. Back up to the start of
        # the anchored line instead: that line is re-read and its anchor is
        # refreshed, so a later append resumes normally instead of rewinding.
        if source_file.size == previous.observed_size:
            recovered = _rewind_to_line_start(source_file, offset=_byte_offset(previous))
            if recovered is not None:
                _emit_cursor_rewound(
                    source_file,
                    previous=previous,
                    new_checkpoint=recovered,
                    branch="resume_checkpoint_anchor_recovered_same_size",
                    previous_checkpoint=previous.checkpoint,
                    read_to_end=previous.read_to_end,
                )
                return recovered
        _emit_cursor_rewound(
            source_file,
            previous=previous,
            new_checkpoint="byte:0",
            branch="resume_checkpoint_anchor_invalid",
            previous_checkpoint=previous.checkpoint,
            read_to_end=previous.read_to_end,
        )
        return None
    return previous.checkpoint


def _anchor_view(source_file: AuthorizedSourceFile, previous: SourceCursor) -> AuthorizedSourceFile:
    """The file as the anchor check should see it.

    Only the anchor comparison uses this, and only when the stored checkpoint
    sits past the size captured at listing time. A host that keeps appending
    leaves the cached size behind, and a checkpoint after it is not really past
    the file: it is past a stale number. Everything else (identity, truncation,
    unchanged filtering) keeps using the listed size, which those paths rely on.
    """
    offset = _byte_offset(previous)
    if offset <= source_file.size:
        return source_file
    try:
        details = os.stat(source_file.path, follow_symlinks=False)
    except OSError:
        return source_file
    return replace(source_file, size=details.st_size)


def _byte_offset(cursor: object) -> int:
    value = str(getattr(cursor, "checkpoint", ""))
    kind, separator, offset = value.partition(":")
    if kind != "byte" or not separator:
        return -1
    try:
        return max(0, int(offset))
    except ValueError:
        return -1


def _rewind_to_line_start(source_file: AuthorizedSourceFile, offset: int) -> str | None:
    """The last record boundary at or before `offset`, as a byte checkpoint.

    Only used when the file's size is unchanged, so the bytes before `offset`
    are still the ones the checkpoint refers to. Returns None when the offset
    is not usable, which leaves the caller to fall back to a full re-read.

    The start of the line that `offset` sits at or just after, so the reader
    replays that one line and refreshes its anchor. Under CRLF a boundary is
    the position after the ``\r\n`` pair, never between ``\r`` and ``\n``. An
    offset at EOF is backed up to the previous line, because EOF can hold a
    half-written line.
    """
    if offset <= 0 or offset > source_file.size:
        return None
    try:
        with source_file.admit().open_binary() as source:
            source.seek(0)
            preceding = source.read(offset)
    except OSError:
        return None
    boundaries = []
    for index in range(len(preceding) - 1, -1, -1):
        if preceding[index : index + 1] == b"\n":
            boundaries.append(index + 1)
    if not boundaries:
        return None
    # `offset` normally sits just past the anchored line, so the first boundary
    # walking backwards is that line's end; the line starts at the one before
    # it. Backing up one more makes the changed line be read again.
    start = boundaries[1] if len(boundaries) > 1 else 0
    return f"byte:{start}"


def _planned_read_bytes(source_file: AuthorizedSourceFile, checkpoint: str | None) -> int:
    if checkpoint is None or not checkpoint.startswith("byte:"):
        return source_file.size
    try:
        offset = int(checkpoint.removeprefix("byte:"))
    except ValueError:
        return source_file.size
    return max(0, source_file.size - max(0, offset))


def jsonl_complete_tail(source_file: AuthorizedSourceFile) -> tuple[str, str | None]:
    """Return the final complete JSONL boundary using one bounded tail read."""
    size = source_file.size
    if size == 0:
        return "byte:0", None
    start = max(0, size - MAX_JSONL_RECORD_BYTES - 2)
    with source_file.admit().open_binary() as source:
        source.seek(start)
        data = source.read(size - start)
    complete = len(data) if data.endswith(b"\n") else data.rfind(b"\n") + 1
    if complete <= 0:
        return "byte:0", None
    body = data[:complete]
    prior = body[:-1].rfind(b"\n")
    if start > 0 and prior < 0:
        return "byte:0", None
    line = body[prior + 1 :]
    return f"byte:{start + complete}", hashlib.sha256(line).hexdigest()


def jsonl_tail_before_latest(source_file: AuthorizedSourceFile) -> tuple[str, str | None]:
    """Leave the newest complete record for the first realtime poll.

    A watcher can fire only after an append. If its first cursor were the
    current EOF, that triggering record would be skipped as historical. At
    most one prior record is replayed when polling starts before any append.
    """
    size = source_file.size
    if size == 0:
        return "byte:0", None
    start = max(0, size - MAX_JSONL_RECORD_BYTES * 2 - 3)
    with source_file.admit().open_binary() as source:
        source.seek(start)
        data = source.read(size - start)
    complete = len(data) if data.endswith(b"\n") else data.rfind(b"\n") + 1
    body = data[:complete]
    latest_start = body[:-1].rfind(b"\n") + 1
    if start > 0 and latest_start <= 0:
        return "byte:0", None
    previous = body[:latest_start]
    if not previous:
        return "byte:0", None
    previous_start = previous[:-1].rfind(b"\n") + 1
    if start > 0 and previous_start <= 0:
        return "byte:0", None
    line = previous[previous_start:]
    return f"byte:{start + latest_start}", hashlib.sha256(line).hexdigest()


def source_cursor(
    source_file: AuthorizedSourceFile,
    checkpoint: str,
    *,
    checkpoint_anchor: str | None = None,
) -> SourceCursor:
    return SourceCursor(
        file_key=source_file.safe_id,
        file_identity=source_file.file_identity,
        checkpoint=checkpoint,
        checkpoint_anchor=checkpoint_anchor,
        observed_size=source_file.size,
        observed_modified_ns=source_file.modified_ns,
    )


_POSITION = re.compile(r"(?<=:)\d+(?=:|$)")


def sortable_ordinal(ordinal: str) -> str:
    """Where a row sits in its source, comparable as text inside one source file.

    Rows that share a timestamp fall back to this column, so `byte:900` must
    not sort after `byte:1000`. Only for rows keyed by message ID: without one,
    the raw ordinal is part of the row's identity and must stay as it was.
    """
    return _POSITION.sub(lambda digits: digits[0].zfill(20), ordinal)


def row_ordinal(ordinal: str, index: int) -> str:
    """Rows split out of one source record keep the record's order among themselves."""
    return ordinal if index == 0 else f"{ordinal}:{index}"


def to_evidence(
    *,
    source_id: str,
    source_file: AuthorizedSourceFile,
    parsed: ParsedMessage,
    ordinal: str,
    source_version: str | None,
) -> EvidenceInput:
    origin = parsed.origin_metadata or OriginMetadata()
    return EvidenceInput(
        source_id=source_id,
        workspace=opaque_workspace_id(source_id, parsed.workspace),
        project=parsed.project or safe_project_label(parsed.workspace),
        conversation_id=parsed.conversation_id,
        message_id=parsed.message_id,
        source_ordinal=sortable_ordinal(ordinal) if parsed.message_id else ordinal,
        content_kind=parsed.content_kind,
        role=parsed.role,
        written_by=parsed.written_by,
        created_at=parsed.created_at,
        verbatim=_clamp_verbatim(parsed.verbatim),
        raw_verbatim=(
            None if parsed.raw_verbatim is None
            else _clamp_verbatim(parsed.raw_verbatim)
        ),
        origin_meta={
            EvidenceOrigin.FILE: OriginMetadata(
                source_version=origin.source_version or source_version,
                raw_meta={
                    **origin.raw_meta,
                    "file_key": source_file.safe_id,
                    "record_type": parsed.record_type,
                },
            )
        },
    )


def _map_read_failure(code: RawReadFailureCode) -> ScanIssueCode:
    if code is RawReadFailureCode.RECORD_TOO_LARGE:
        return ScanIssueCode.RECORD_TOO_LARGE
    return ScanIssueCode.INVALID_JSON


def discovery_failure_code(
    failure: SourceFileDiscoveryFailure,
) -> ScanIssueCode:
    if failure.reason_code is SourceAccessErrorCode.BUDGET_EXCEEDED:
        return ScanIssueCode.BUDGET_EXCEEDED
    if failure.reason_code is SourceAccessErrorCode.FILE_TOO_LARGE:
        # The file is readable and its format is fine; only its size is over
        # the access gate. Reporting that as `unsupported_schema` sends the
        # user looking for a parser problem that does not exist.
        return ScanIssueCode.FILE_TOO_LARGE
    return ScanIssueCode.UNSUPPORTED_SCHEMA


def _scan_failure_code(code: SourceAccessErrorCode) -> ScanIssueCode:
    """Map a per-file open failure onto a machine code, never onto free text."""
    if code is SourceAccessErrorCode.BUDGET_EXCEEDED:
        return ScanIssueCode.BUDGET_EXCEEDED
    if code is SourceAccessErrorCode.FILE_TOO_LARGE:
        return ScanIssueCode.FILE_TOO_LARGE
    if code is SourceAccessErrorCode.SYMLINK_NOT_ALLOWED:
        return ScanIssueCode.UNSUPPORTED_SCHEMA
    if code in {
        SourceAccessErrorCode.SOURCE_UNAVAILABLE,
        SourceAccessErrorCode.NOT_REGULAR_FILE,
        SourceAccessErrorCode.ROOT_NOT_DIRECTORY,
    }:
        return ScanIssueCode.SOURCE_BUSY
    return ScanIssueCode.UNSUPPORTED_SCHEMA
