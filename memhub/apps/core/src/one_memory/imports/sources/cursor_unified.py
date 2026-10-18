import asyncio
import hashlib
import json
import os
import re
import sqlite3
import stat
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from functools import partial

from pydantic import JsonValue

from one_memory.imports.access import AuthorizedSourceFile, AuthorizedSourceRoot, SourceAccessError
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
    SourceCursor,
    SourceDescriptor,
    StorageKind,
)
from one_memory.imports.async_bridge import iterate_in_worker
from one_memory.imports.discovery import (
    SourceFileDiscoveryFailure,
    discover_source_files,
    discovered_off_loop,
)
from one_memory.imports.models import OriginMetadata, WrittenBy
from one_memory.imports.sources.claude_code import TOOL_BLOCKS
from one_memory.imports.sources.common import (
    STOP_SCAN_ACCESS_CODES,
    JsonlSourceAdapter,
    ParsedMessage,
    ParsedSkip,
    SourceInstallState,
    SourceRecordContext,
    discovery_failure_code,
    followed_by_tool,
    is_unchanged,
    jsonl_complete_tail,
    newest_first,
    resume_checkpoint,
    rotate_history_files,
    source_cursor,
    to_evidence,
    touched_cursor,
)
from one_memory.imports.sources.cursor import CursorSourceAdapter
from one_memory.imports.sources.parsing import (
    as_record,
    is_host_generated_user_turn,
    strip_host_wrappers,
    text_content,
    tool_blocks_evidence,
)
from one_memory.imports.sqlite_access import open_sqlite_readonly

CURSOR_UNIFIED_DESCRIPTOR = SourceDescriptor(
    source_id="cursor",
    display_name="Cursor",
    storage_kinds=frozenset({StorageKind.JSONL, StorageKind.SQLITE}),
    capabilities=SourceCapabilities(
        can_append=True,
        can_observe_edits=False,
        can_observe_deletes=False,
        can_reconcile_complete_set=False,
    ),
)

_TEXT_BLOCKS = frozenset({"text", "input_text", "output_text"})
_CURSOR_HOST_CONTEXT_START = re.compile(
    r"<(?:user_info|dynamic_tools|dynamic_tool_catalog|system[-_]reminder)\b"
)
_CURSOR_USER_QUERY = re.compile(r"<user_query\b")
_CURSOR_TIMESTAMP_PREFIX = re.compile(r"<timestamp>[\s\S]*?</timestamp>\s*")
_CURSOR_TIMESTAMP_VALUE = re.compile(
    r"<timestamp>\s*"
    r"([A-Za-z]+,\s+[A-Za-z]{3}\s+\d{1,2},\s+\d{4},\s+\d{1,2}:\d{2}\s+[AP]M)"
    r"\s+\(UTC([+-]\d{1,2})(?::(\d{2}))?\)\s*</timestamp>",
)
_CURSOR_IMAGE_FILES_PREFIX = re.compile(r"\[Image\]\s*<image_files>[\s\S]*?</image_files>\s*")
_CURSOR_BARE_IMAGE_PREFIX = re.compile(r"(?:\[Image\]\s*)+")


# Cursor writes thinking and answer into one text block with no field to tell
# them apart; a blank line is the only separator it gives us. The cue words are
# the ones the harness actually opens with, so an answer that genuinely starts
# "I need to ..." is still kept: only a tail that opens this way is dropped.
_THINKING_OPENING = re.compile(
    r"(?i)^\s*(?:"
    r"i\s+(?:need|'?ll|should|might|must|want|have)\b|"
    r"i\s*'?m\s+(?:going|planning|organizing|working|thinking|considering)\b|"
    r"let'?s\b|the user (?:wants|needs|seems|is asking)\b|"
    r"(?:okay|ok|so|now|first|then)\s*,?\s+i\b"
    r")"
)
_THINKING_CUE = re.compile(r"(?i)\b(?:let'?s|i need to|i should|i'?ll need to)\b")
# Longer than an answer is likely to be, but well under a real reasoning dump.
_MAX_THINKING_CHARACTERS = 4_000


def strip_thinking(content: str) -> str:
    """Drop Cursor's trailing thinking tail, keeping the answer it precedes."""
    separator = "\n\n"
    if separator not in content:
        return content
    head, _, tail = content.rpartition(separator)
    head = head.strip()
    tail = tail.strip()
    # Without a substantial answer ahead of it, this is one block, not a tail.
    if not head or len(tail) > _MAX_THINKING_CHARACTERS:
        return content
    if not _THINKING_OPENING.match(tail) and not _THINKING_CUE.search(tail[:200]):
        return content
    return head


def _strip_cursor_timestamp(content: str | None) -> str | None:
    return _CURSOR_TIMESTAMP_PREFIX.sub("", content, count=1) if content else None


def _cursor_host_timestamp(content: str | None) -> datetime | None:
    """Parse Cursor's user-visible timestamp envelope into UTC."""
    if not content:
        return None
    match = _CURSOR_TIMESTAMP_VALUE.search(content)
    if match is None:
        return None
    try:
        local = datetime.strptime(match[1], "%A, %b %d, %Y, %I:%M %p")
        hours = int(match[2])
        minutes = int(match[3] or "0")
        sign = -1 if hours < 0 else 1
        offset = timedelta(hours=hours, minutes=sign * minutes)
        return local.replace(tzinfo=timezone(offset)).astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def _strip_cursor_host_prefix(content: str | None) -> str | None:
    if not content:
        return content
    without_image = _CURSOR_IMAGE_FILES_PREFIX.sub("", content, count=1)
    without_image = _CURSOR_BARE_IMAGE_PREFIX.sub("", without_image, count=1)
    return _strip_cursor_timestamp(without_image)


def _is_cursor_host_context(content: str) -> bool:
    return (
        _CURSOR_HOST_CONTEXT_START.match(content) is not None
        and _CURSOR_USER_QUERY.search(content) is None
    )


class CursorUnifiedSourceAdapter:
    """Cursor adapter spanning current transcripts/store.db and legacy state.vscdb."""

    def __init__(
        self,
        *,
        grant: AuthorizedSourceRoot,
        source_version: str | None = None,
    ) -> None:
        if grant.source_id != CURSOR_UNIFIED_DESCRIPTOR.source_id:
            raise ValueError("Cursor grant must use the cursor source ID")
        self.grant = grant
        self.source_version = source_version
        self._history_listing_cache: _CursorListing | None = None

    @property
    def descriptor(self) -> SourceDescriptor:
        return CURSOR_UNIFIED_DESCRIPTOR

    async def detect(self) -> bool:
        return (await self.install_state()).detected

    async def install_state(self) -> SourceInstallState:
        installed = await asyncio.to_thread(self.grant.root.is_dir)
        if not installed:
            return SourceInstallState(
                installed=False,
                session_count=0,
                detected=False,
            )
        listing = await asyncio.to_thread(
            _list_cursor_files,
            self.grant,
            max_targets=100_000,
            cursors={},
        )
        return SourceInstallState(
            installed=True,
            session_count=listing.total,
            detected=True,
        )

    async def count_sessions(self, *, limit: int | None = None) -> int:
        state = await self.install_state()
        return min(state.session_count, limit) if limit is not None else state.session_count

    async def live_baseline(self) -> dict[str, SourceCursor]:
        """Capture Cursor's current tail so the realtime lane never backfills history."""
        listing = await asyncio.to_thread(
            _list_cursor_files,
            self.grant,
            max_targets=100_000,
            cursors={},
        )
        return await asyncio.to_thread(_live_baseline, listing)

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
        if listing.transcripts or listing.stores or listing.legacy:
            yield ScanProgressEvent(phase=ScanPhase.READ)
        emitted = 0

        def reached_limit(event: ScanEvent) -> bool:
            nonlocal emitted
            if isinstance(event, ScannedEvidence):
                emitted += 1
            return options.max_messages is not None and emitted >= options.max_messages

        transcript_adapter = JsonlSourceAdapter(
            descriptor=CURSOR_UNIFIED_DESCRIPTOR,
            grant=self.grant,
            parser=partial(_parse_transcript_record, subagent_ids=listing.subagent_ids),
            source_version=self.source_version,
        )
        async for event in transcript_adapter.read_listed(
            listing.transcripts, options, announce=False
        ):
            yield event
            if reached_limit(event):
                return

        for listed in listing.stores:
            try:
                store = await asyncio.to_thread(listed.admit)
            except SourceAccessError as error:
                if error.code in STOP_SCAN_ACCESS_CODES:
                    raise
                continue
            last: SourceCursor | None = None
            failed = False
            try:
                async for event in iterate_in_worker(
                    partial(
                        _read_store_messages,
                        store,
                        options,
                        conversation_id=store.relative_path.parent.name,
                        written_by_user=(
                            WrittenBy.PERSON
                            if "chats" in store.relative_path.parts
                            else WrittenBy.UNKNOWN
                        ),
                        source_version=self.source_version,
                    ),
                    scope_key=self.descriptor.source_id,
                ):
                    if isinstance(event, ScanFailure):
                        failed = True
                    elif not isinstance(event, ScanProgressEvent | ScanCursorTouch):
                        last = event.cursor
                    yield event
                    if reached_limit(event):
                        return
                    if emitted and emitted % 100 == 0:
                        await asyncio.sleep(0)
            except SourceAccessError as error:
                if error.code in STOP_SCAN_ACCESS_CODES:
                    raise
                continue
            if not failed:
                yield ScanCursorTouch(
                    cursor=touched_cursor(store, options, last=last, empty_checkpoint="rowid:0")
                )

        legacy_adapter = CursorSourceAdapter(grant=self.grant, source_version=self.source_version)
        async for event in legacy_adapter.read_listed(listing.legacy, options):
            yield _shadowed_by_transcript(event, listing.transcript_ids)
            if reached_limit(event):
                return
        yield ScanProgressEvent(phase=ScanPhase.DONE)

    def _history_listing(self, options: ScanOptions) -> "_CursorListing":
        cached = self._history_listing_cache
        if cached is not None:

            def remaining(files: list[AuthorizedSourceFile]) -> list[AuthorizedSourceFile]:
                result = [item for item in files if not is_unchanged(item, options.cursors)]
                return rotate_history_files(result, options.cursors)

            transcripts = remaining(cached.transcripts)
            stores = remaining(cached.stores)
            legacy = remaining(cached.legacy)
            if transcripts or stores or legacy:
                return _CursorListing(
                    total=cached.total,
                    transcripts=transcripts,
                    stores=stores,
                    legacy=legacy,
                    transcript_ids=cached.transcript_ids,
                    subagent_ids=cached.subagent_ids,
                    failures=[],
                )
            self._history_listing_cache = None
        listing = self._fresh_listing(options)
        if (listing.transcripts or listing.stores or listing.legacy) and not listing.failures:
            self._history_listing_cache = listing
        return listing

    def _fresh_listing(self, options: ScanOptions) -> "_CursorListing":
        return _list_cursor_files(
            self.grant,
            max_targets=options.max_scan_targets or 100_000,
            cursors=options.cursors,
        )

    async def _scan_frozen(self, options: ScanOptions) -> AsyncIterator[ScanEvent]:
        """A caller-owned immutable copy: the snapshot store's path, not the import path."""
        # Both walk the whole of Cursor's data directory, which is the largest
        # of these on a real machine. Kept here on the loop they were seconds
        # of it, before a single record had been read.
        selected_transcripts = await asyncio.to_thread(_select_transcript_files, self.grant)
        transcript_ids = {
            _transcript_conversation_id(source_file)
            for source_file in selected_transcripts.values()
        }
        transcript_project_ids = {
            _transcript_project_id(source_file) for source_file in selected_transcripts.values()
        }
        transcript_file_ids = {source_file.safe_id for source_file in selected_transcripts.values()}
        subagent_ids = await asyncio.to_thread(_subagent_conversation_ids, self.grant)
        emitted = 0

        transcript_adapter = JsonlSourceAdapter(
            descriptor=CURSOR_UNIFIED_DESCRIPTOR,
            grant=self.grant,
            parser=partial(
                _parse_transcript_record,
                subagent_ids=subagent_ids,
            ),
            source_version=self.source_version,
            file_predicate=lambda source_file: source_file.safe_id in transcript_file_ids,
        )
        async for event in transcript_adapter.scan(options):
            if _is_done(event):
                continue
            yield event
            if isinstance(event, ScannedEvidence):
                emitted += 1
                if options.max_messages is not None and emitted >= options.max_messages:
                    return

        async for discovered in discovered_off_loop(
            self.grant,
            suffixes=frozenset({".db"}),
            max_targets=options.max_scan_targets or 100_000,
        ):
            if isinstance(discovered, SourceFileDiscoveryFailure):
                yield ScanFailure(reason_code=discovery_failure_code(discovered))
                continue
            if not _is_store_file(discovered):
                continue
            yield ScanProgressEvent(phase=ScanPhase.DISCOVER, discovered_increment=1)
            conversation_id = discovered.relative_path.parent.name
            if (
                _store_project_id(discovered) in transcript_project_ids
                and conversation_id in transcript_ids
            ):
                continue
            written_by_user = (
                WrittenBy.PERSON if "chats" in discovered.relative_path.parts else WrittenBy.UNKNOWN
            )
            async for event in iterate_in_worker(
                partial(
                    _read_store_messages,
                    discovered,
                    options,
                    conversation_id=conversation_id,
                    written_by_user=written_by_user,
                    source_version=self.source_version,
                ),
                scope_key=self.descriptor.source_id,
            ):
                yield event
                if isinstance(event, ScannedEvidence):
                    emitted += 1
                    if options.max_messages is not None and emitted >= options.max_messages:
                        return
                if emitted and emitted % 100 == 0:
                    await asyncio.sleep(0)

        legacy_adapter = CursorSourceAdapter(
            grant=self.grant,
            source_version=self.source_version,
        )
        async for event in legacy_adapter.scan(options):
            if _is_done(event):
                continue
            if (
                isinstance(event, ScannedEvidence)
                and event.evidence.conversation_id in transcript_ids
                and not (
                    event.evidence.written_by is WrittenBy.AGENT
                    and is_host_generated_user_turn(event.evidence.verbatim)
                )
            ):
                yield ScanSkip(
                    cursor=event.cursor,
                    conversation_id=event.evidence.conversation_id,
                    reason_code=ScanIssueCode.UNKNOWN_RECORD,
                )
                continue
            yield event
            if isinstance(event, ScannedEvidence):
                emitted += 1
                if options.max_messages is not None and emitted >= options.max_messages:
                    return

        yield ScanProgressEvent(phase=ScanPhase.DONE)


def _is_done(event: ScanEvent) -> bool:
    return isinstance(event, ScanProgressEvent) and event.phase is ScanPhase.DONE


def _shadowed_by_transcript(event: ScanEvent, transcript_ids: set[str]) -> ScanEvent:
    if (
        isinstance(event, ScannedEvidence)
        and event.evidence.conversation_id in transcript_ids
        and not (
            event.evidence.written_by is WrittenBy.AGENT
            and is_host_generated_user_turn(event.evidence.verbatim)
        )
    ):
        return ScanSkip(
            cursor=event.cursor,
            conversation_id=event.evidence.conversation_id,
            reason_code=ScanIssueCode.UNKNOWN_RECORD,
        )
    return event


@dataclass(frozen=True)
class _CursorListing:
    total: int
    transcripts: list[AuthorizedSourceFile]
    stores: list[AuthorizedSourceFile]
    legacy: list[AuthorizedSourceFile]
    transcript_ids: set[str]
    subagent_ids: set[str]
    failures: list[SourceFileDiscoveryFailure]


def _list_cursor_files(
    grant: AuthorizedSourceRoot, *, max_targets: int, cursors: dict[str, SourceCursor]
) -> _CursorListing:
    """One walk of Cursor's directory, stat only; changed files come back newest first."""
    selected: dict[str, AuthorizedSourceFile] = {}
    subagent_ids: set[str] = set()
    stores: list[AuthorizedSourceFile] = []
    legacy: list[AuthorizedSourceFile] = []
    failures: list[SourceFileDiscoveryFailure] = []
    for item in discover_source_files(
        grant, suffixes=frozenset({".jsonl", ".db", ".vscdb"}), max_targets=max_targets
    ):
        if isinstance(item, SourceFileDiscoveryFailure):
            failures.append(item)
        elif _is_transcript_file(item):
            if "subagents" in item.relative_path.parts:
                subagent_ids.add(_transcript_file_identity(item))
            identity = _transcript_file_identity(item)
            existing = selected.get(identity)
            if existing is None or (item.size, item.modified_ns) > (
                existing.size,
                existing.modified_ns,
            ):
                selected[identity] = item
        elif _is_store_file(item):
            stores.append(_with_write_ahead_log(item))
        elif item.relative_path.name == "state.vscdb":
            legacy.append(_with_write_ahead_log(item))
    transcript_ids = {_transcript_conversation_id(item) for item in selected.values()}
    transcript_project_ids = {_transcript_project_id(item) for item in selected.values()}
    stores = [
        item
        for item in stores
        if _store_project_id(item) not in transcript_project_ids
        or item.relative_path.parent.name not in transcript_ids
    ]
    listed = [*selected.values(), *stores, *legacy]
    return _CursorListing(
        total=len(listed),
        transcripts=newest_first(
            [item for item in selected.values() if not is_unchanged(item, cursors)]
        ),
        stores=newest_first([item for item in stores if not is_unchanged(item, cursors)]),
        legacy=newest_first([item for item in legacy if not is_unchanged(item, cursors)]),
        transcript_ids=transcript_ids,
        subagent_ids=subagent_ids,
        failures=failures,
    )


def _with_write_ahead_log(database: AuthorizedSourceFile) -> AuthorizedSourceFile:
    """A database's rows can change in its -wal alone; its own mtime would miss that."""
    try:
        journal = os.lstat(database.path.with_name(database.path.name + "-wal"))
    except OSError:
        return database
    if not stat.S_ISREG(journal.st_mode):
        return database
    # SQLite can append several committed frames without changing the main
    # database and, on coarse filesystems, without advancing WAL mtime. Fold
    # all observable WAL identity into the cursor's comparison token. The
    # security recheck uses `changed_ns`, which remains the main file's real
    # ctime, so this synthetic value cannot weaken path validation.
    version = hashlib.blake2b(
        f"{database.modified_ns}:{journal.st_mtime_ns}:{journal.st_size}:{journal.st_ino}".encode(),
        digest_size=8,
    ).digest()
    return replace(database, modified_ns=int.from_bytes(version, "big") & ((1 << 63) - 1))


def _live_baseline(listing: _CursorListing) -> dict[str, SourceCursor]:
    cursors: dict[str, SourceCursor] = {}
    for source_file in listing.transcripts:
        try:
            checkpoint, anchor = jsonl_complete_tail(source_file)
        except (OSError, SourceAccessError):
            continue
        cursors[source_file.safe_id] = source_cursor(
            source_file, checkpoint, checkpoint_anchor=anchor
        ).model_copy(update={"read_to_end": checkpoint == f"byte:{source_file.size}"})
    for source_file in listing.stores:
        try:
            admitted = source_file.admit()
            with open_sqlite_readonly(admitted) as connection:
                row = connection.execute(
                    "SELECT rowid, id FROM blobs ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
        except (OSError, sqlite3.Error, SourceAccessError):
            continue
        rowid = int(row["rowid"]) if row is not None else 0
        anchor = _checkpoint_anchor(str(row["id"])) if row is not None else None
        cursors[source_file.safe_id] = source_cursor(
            source_file, f"rowid:{rowid}", checkpoint_anchor=anchor
        ).model_copy(update={"read_to_end": True})
    for source_file in listing.legacy:
        try:
            admitted = source_file.admit()
            with open_sqlite_readonly(admitted) as connection:
                row = connection.execute(
                    "SELECT rowid, key FROM cursorDiskKV ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
        except (OSError, sqlite3.Error, SourceAccessError):
            continue
        rowid = int(row["rowid"]) if row is not None else 0
        anchor = _checkpoint_anchor(str(row["key"])) if row is not None else None
        cursors[source_file.safe_id] = source_cursor(
            source_file, f"row:{rowid}", checkpoint_anchor=anchor
        ).model_copy(update={"read_to_end": True})
    return cursors


def _is_cursor_data_file(source_file: AuthorizedSourceFile) -> bool:
    return (
        _is_transcript_file(source_file)
        or _is_store_file(source_file)
        or source_file.relative_path.name == "state.vscdb"
    )


def _is_transcript_file(source_file: AuthorizedSourceFile) -> bool:
    parts = source_file.relative_path.parts
    return source_file.relative_path.suffix == ".jsonl" and "agent-transcripts" in parts


def _transcript_project_id(source_file: AuthorizedSourceFile) -> str:
    parts = source_file.relative_path.parts
    if "projects" in parts:
        index = parts.index("projects")
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


def _transcript_conversation_id(source_file: AuthorizedSourceFile) -> str:
    return source_file.relative_path.stem


def _transcript_file_identity(source_file: AuthorizedSourceFile) -> str:
    """One identity per project/session, regardless of copy location."""
    return f"{_transcript_project_id(source_file)}\0{_transcript_conversation_id(source_file)}"


def _store_project_id(source_file: AuthorizedSourceFile) -> str:
    parts = source_file.relative_path.parts
    for marker in ("chats", "acp-sessions"):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    return ""


def _is_store_file(source_file: AuthorizedSourceFile) -> bool:
    parts = source_file.relative_path.parts
    return source_file.relative_path.name == "store.db" and (
        "chats" in parts or "acp-sessions" in parts
    )


def _select_transcript_files(
    grant: AuthorizedSourceRoot,
) -> dict[str, AuthorizedSourceFile]:
    selected: dict[str, AuthorizedSourceFile] = {}
    for item in discover_source_files(
        grant,
        suffixes=frozenset({".jsonl"}),
        max_targets=100_000,
    ):
        if not isinstance(item, AuthorizedSourceFile) or not _is_transcript_file(item):
            continue
        identity = _transcript_file_identity(item)
        existing = selected.get(identity)
        if existing is None or (item.size, item.modified_ns) > (
            existing.size,
            existing.modified_ns,
        ):
            selected[identity] = item
    return selected


def _subagent_conversation_ids(grant: AuthorizedSourceRoot) -> set[str]:
    return {
        _transcript_file_identity(item)
        for item in discover_source_files(
            grant,
            suffixes=frozenset({".jsonl"}),
            max_targets=100_000,
        )
        if isinstance(item, AuthorizedSourceFile)
        and _is_transcript_file(item)
        and "subagents" in item.relative_path.parts
    }


def _parse_transcript_record(
    record: dict[str, JsonValue],
    context: SourceRecordContext,
    *,
    subagent_ids: set[str],
) -> ParsedMessage | ParsedSkip:
    role = record.get("role")
    if role not in {"user", "assistant"}:
        return ParsedSkip(reason_code=ScanIssueCode.UNKNOWN_RECORD)
    message = as_record(record.get("message"))
    if message is None:
        return ParsedSkip(reason_code=ScanIssueCode.NO_TEXT)
    path_parts = context.relative_path.split("/")
    conversation_id = context.relative_path.rsplit("/", 1)[-1].removesuffix(".jsonl")
    workspace = (
        path_parts[path_parts.index("projects") + 1]
        if "projects" in path_parts and path_parts.index("projects") + 1 < len(path_parts)
        else None
    )
    content = text_content(message.get("content"), allowed_block_types=_TEXT_BLOCKS)
    if content is None:
        tool_text = tool_blocks_evidence(message.get("content"), block_types=TOOL_BLOCKS)
        if tool_text is None:
            return ParsedSkip(reason_code=ScanIssueCode.TOOL_CONTENT_EXCLUDED)
        native_message_id = record.get("id") or message.get("id")
        return ParsedMessage(
            conversation_id=conversation_id,
            message_id=(
                str(native_message_id)
                if isinstance(native_message_id, str) and native_message_id
                else f"cursor-transcript:{context.ordinal}"
            ),
            role="tool",
            written_by=WrittenBy.TOOL,
            verbatim=tool_text,
            raw_verbatim=tool_text,
            created_at=_transcript_fallback_time(context),
            workspace=workspace,
            record_type="cursor_transcript.tool",
            origin_metadata=OriginMetadata(raw_meta={"time_basis": "file_mtime"}),
        )
    without_host_prefix = _strip_cursor_host_prefix(content)
    if without_host_prefix is None:
        return ParsedSkip(reason_code=ScanIssueCode.NO_TEXT)
    cleaned = strip_host_wrappers(without_host_prefix)
    if cleaned is None:
        return ParsedSkip(reason_code=ScanIssueCode.NO_TEXT)
    # Thinking rides in the same block as the answer, so an assistant body ends
    # with reasoning the person never saw. Identity hashes `content`, the
    # pre-cleaning text, so dropping it here cannot re-key stored rows.
    if role == "assistant":
        cleaned = strip_thinking(cleaned)
        if not cleaned:
            return ParsedSkip(reason_code=ScanIssueCode.NO_TEXT)
    if role == "user" and _is_cursor_host_context(cleaned):
        return ParsedSkip(reason_code=ScanIssueCode.UNKNOWN_RECORD)
    project_name = path_parts[path_parts.index("projects") + 1] if "projects" in path_parts else ""
    session_name = context.relative_path.rsplit("/", 1)[-1].removesuffix(".jsonl")
    is_subagent = f"{project_name}\0{session_name}" in subagent_ids
    host_timestamp = _cursor_host_timestamp(content) if role == "user" else None
    created_at = _transcript_fallback_time(context)
    native_message_id = record.get("id") or message.get("id")
    message_id = (
        str(native_message_id)
        if isinstance(native_message_id, str) and native_message_id
        else f"cursor-transcript:{context.ordinal}"
    )
    return followed_by_tool(
        ParsedMessage(
            conversation_id=conversation_id,
            message_id=message_id,
            role=role,
            written_by=(
                WrittenBy.AGENT
                if is_subagent or role == "assistant" or is_host_generated_user_turn(cleaned)
                else WrittenBy.PERSON
            ),
            verbatim=cleaned,
            # The transcript text before our cleaning stripped the host wrapper.
            # Identity hashes this so the hook lane, which sees no wrapper, and
            # this lane converge on one key for the same words.
            raw_verbatim=content,
            created_at=created_at,
            workspace=workspace,
            record_type="cursor_transcript",
            origin_metadata=OriginMetadata(
                raw_meta={
                    "time_basis": "file_mtime",
                    **(
                        {"turn_event_at": host_timestamp.isoformat()}
                        if host_timestamp is not None
                        else {}
                    ),
                }
            ),
        ),
        tool_blocks_evidence(message.get("content"), block_types=TOOL_BLOCKS),
    )


def _transcript_fallback_time(context: SourceRecordContext) -> datetime:
    """Use file mtime while preserving deterministic record order within the file."""
    checkpoint = context.ordinal.rsplit(":", 1)[-1]
    try:
        offset = max(0, int(checkpoint))
    except ValueError:
        offset = 0
    modified = datetime.fromtimestamp(context.modified_ns / 1_000_000_000, tz=UTC)
    return modified + timedelta(microseconds=offset)


def _read_store_messages(
    source_file: AuthorizedSourceFile,
    options: ScanOptions,
    *,
    conversation_id: str,
    written_by_user: WrittenBy,
    source_version: str | None,
) -> Iterator[ScanEvent]:
    checkpoint = resume_checkpoint(source_file, options)
    last_rowid = _row_checkpoint(checkpoint)
    previous = options.cursors.get(source_file.safe_id)
    try:
        with open_sqlite_readonly(source_file) as connection:
            conversation_id = _store_agent_id(connection) or conversation_id
            last_rowid = _validated_store_checkpoint(
                connection,
                last_rowid=last_rowid,
                checkpoint_anchor=(previous.checkpoint_anchor if previous is not None else None),
            )
            rows = connection.execute(
                "SELECT rowid, id, data FROM blobs WHERE rowid > ? ORDER BY rowid",
                (last_rowid,),
            )
            for row in rows:
                rowid = int(row["rowid"])
                cursor = source_cursor(
                    source_file,
                    f"rowid:{rowid}",
                    checkpoint_anchor=_checkpoint_anchor(str(row["id"])),
                )
                try:
                    payload = json.loads(row["data"])
                except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
                    # Cursor keeps content-addressed protobuf/tool blobs beside
                    # message envelopes. They are not independently attributable
                    # chat turns and must not be treated as corrupt messages.
                    yield ScanSkip(
                        cursor=cursor,
                        conversation_id=conversation_id,
                        reason_code=ScanIssueCode.UNKNOWN_RECORD,
                    )
                    continue
                record = as_record(payload)
                role = record.get("role") if record else None
                if record is None or role not in {"user", "assistant"}:
                    yield ScanSkip(
                        cursor=cursor,
                        conversation_id=conversation_id,
                        reason_code=ScanIssueCode.UNKNOWN_RECORD,
                    )
                    continue
                content = text_content(
                    record.get("content"),
                    allowed_block_types=_TEXT_BLOCKS,
                )
                cursor_content = _strip_cursor_host_prefix(content)
                cleaned = strip_host_wrappers(cursor_content) if cursor_content else None
                if cleaned is None:
                    yield ScanSkip(
                        cursor=cursor,
                        conversation_id=conversation_id,
                        reason_code=ScanIssueCode.TOOL_CONTENT_EXCLUDED,
                    )
                    continue
                if role == "user" and _is_cursor_host_context(cleaned):
                    # Cursor stores host session context (user_info plus tool
                    # catalog data) as a role=user blob with no user_query.
                    # The transport role does not prove the person wrote it.
                    yield ScanSkip(
                        cursor=cursor,
                        conversation_id=conversation_id,
                        reason_code=ScanIssueCode.UNKNOWN_RECORD,
                    )
                    continue
                host_timestamp = (
                    _cursor_host_timestamp(content) if role == "user" else None
                )
                fallback_timestamp = datetime.fromtimestamp(
                    source_file.modified_ns / 1_000_000_000,
                    tz=UTC,
                ) + timedelta(microseconds=rowid)
                parsed = ParsedMessage(
                    conversation_id=conversation_id,
                    message_id=str(record.get("id") or row["id"]),
                    role=role,
                    written_by=(
                        WrittenBy.AGENT
                        if role != "user" or is_host_generated_user_turn(cleaned)
                        else written_by_user
                    ),
                    verbatim=cleaned,
                    created_at=fallback_timestamp,
                    workspace=_store_workspace(source_file),
                    record_type="cursor_store_blob",
                    origin_metadata=OriginMetadata(
                        raw_meta={
                            "time_basis": "file_mtime",
                            **(
                                {"turn_event_at": host_timestamp.isoformat()}
                                if host_timestamp is not None
                                else {}
                            ),
                        }
                    ),
                )
                yield ScannedEvidence(
                    cursor=cursor,
                    evidence=to_evidence(
                        source_id=CURSOR_UNIFIED_DESCRIPTOR.source_id,
                        source_file=source_file,
                        parsed=parsed,
                        ordinal=f"{source_file.safe_id}:rowid:{rowid}",
                        source_version=source_version,
                    ),
                )
    except sqlite3.Error:
        yield ScanFailure(reason_code=ScanIssueCode.UNSUPPORTED_SCHEMA)


def _store_agent_id(connection: sqlite3.Connection) -> str | None:
    """Cursor's meta row carries the stable agent id when the directory is moved."""
    try:
        row = connection.execute("SELECT value FROM meta LIMIT 1").fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    raw = row["value"] if isinstance(row, sqlite3.Row) else row[0]
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", "replace")
    elif isinstance(raw, str):
        text = raw
    else:
        return None
    try:
        decoded = bytes.fromhex(text).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        decoded = text
    try:
        record = json.loads(decoded)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    agent_id = record.get("agentId")
    return agent_id if isinstance(agent_id, str) and agent_id.strip() else None


def _store_workspace(source_file: AuthorizedSourceFile) -> str | None:
    parts = source_file.relative_path.parts
    for marker in ("chats", "acp-sessions"):
        if marker in parts:
            index = parts.index(marker)
            return parts[index + 1] if index + 1 < len(parts) else None
    return None


def _row_checkpoint(checkpoint: str | None) -> int:
    if checkpoint is None or not checkpoint.startswith("rowid:"):
        return 0
    try:
        return max(0, int(checkpoint.removeprefix("rowid:")))
    except ValueError:
        return 0


def _validated_store_checkpoint(
    connection: sqlite3.Connection,
    *,
    last_rowid: int,
    checkpoint_anchor: str | None,
) -> int:
    if last_rowid <= 0 or checkpoint_anchor is None:
        return 0
    row = connection.execute(
        "SELECT id FROM blobs WHERE rowid = ?",
        (last_rowid,),
    ).fetchone()
    if row is None or _checkpoint_anchor(str(row["id"])) != checkpoint_anchor:
        return 0
    return last_rowid


def _checkpoint_anchor(blob_id: str) -> str:
    return hashlib.sha256(blob_id.encode()).hexdigest()
