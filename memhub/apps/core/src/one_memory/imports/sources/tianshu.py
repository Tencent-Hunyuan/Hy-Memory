"""Rivet (天枢 / Tianshu) session adapter.

Rivet stores each session under ``~/.rivet/sessions/<uuid>.jsonl`` plus a
sibling ``<uuid>.meta.json``. Despite the ``.jsonl`` extension the transcript
file is a sequence of zstd frames: each decompressed line is one turn,

    {"role": "user" | "assistant", "content": "...", "origin": "human" | ...}|<hex checksum>

The ``|<hex>`` suffix is a host-side integrity tag; :func:`read_rivet_jsonl`
strips it before JSON decoding. The sibling ``.meta.json`` carries the
session's ``cwd``, ``model``, ``createdAt`` and ``sessionId`` — none of which
appear in the per-turn records — so the adapter loads it once per file and
hands it to the parser as the record context header.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

from pydantic import JsonValue

from one_memory.imports.access import (
    AuthorizedSourceFile,
    AuthorizedSourceRoot,
)
from one_memory.imports.readers import read_rivet_jsonl
from one_memory.imports.sources.common import (
    JsonlSourceAdapter,
    ParsedMessage,
    ParsedSkip,
    SourceRecordContext,
    resume_checkpoint,
)
from one_memory.imports.sources.parsing import (
    conversation_from_relative_path,
    first_string,
    parse_timestamp,
)
from one_memory.imports.adapters import (
    ScanIssueCode,
    SourceCapabilities,
    SourceDescriptor,
    StorageKind,
)
from one_memory.imports.models import OriginMetadata, WrittenBy

_log = logging.getLogger(__name__)

TIANSHU_DESCRIPTOR = SourceDescriptor(
    source_id="tianshu",
    display_name="天枢",
    storage_kinds=frozenset({StorageKind.ZSTD, StorageKind.JSONL}),
    capabilities=SourceCapabilities(
        can_append=True,
        can_observe_edits=True,
        can_observe_deletes=False,
        can_reconcile_complete_set=False,
    ),
)

#: Fields copied from ``.meta.json`` into each record's origin metadata. Kept
#: to the provenance a memory needs (where it ran, which model, which session)
#: and nothing the host stamps for its own bookkeeping (tokenUsage,
#: guardianActivity, planModeState, ...).
_RIVET_SESSION_PROVENANCE_FIELDS = (
    "sessionId",
    "model",
    "domain",
    "title",
)


class RivetSourceAdapter(JsonlSourceAdapter):
    """A :class:`JsonlSourceAdapter` that reads Rivet's zstd-framed ``.jsonl``.

    Rivet names its transcripts ``.jsonl`` but each is a sequence of zstd
    frames whose decompressed lines carry a ``|<hex>`` checksum suffix. Both
    traits break the base class's defaults (plain-text ``read_jsonl`` and
    ``byte:`` checkpoints), so we mark every file as zstd-framed — which
    switches the reader to :func:`read_zstd_jsonl`'s streaming loop and the
    cursor to ``record:`` counting — and override the reader to the rivet
    variant that strips the checksum. The sibling ``.meta.json`` is folded
    into the parser header so each turn inherits the session's ``cwd``,
    ``model`` and ``createdAt``.
    """

    def _is_zstd_framed(self, source_file: AuthorizedSourceFile) -> bool:
        return True

    def _reader_for(self, source_file: AuthorizedSourceFile):  # type: ignore[override]
        return read_rivet_jsonl

    def _prepare(
        self,
        listed: AuthorizedSourceFile,
        options,  # ScanOptions
    ) -> tuple[AuthorizedSourceFile, str | None, dict[str, JsonValue] | None]:
        source_file = listed.admit()
        checkpoint = resume_checkpoint(source_file, options)
        reader = self._reader_for(source_file)
        header = (
            None if self.header_selector is None else self._header_for(source_file, reader)
        )
        meta = self._load_meta(source_file)
        if meta is not None:
            header = {**(header or {}), **meta}
        return source_file, checkpoint, header

    @staticmethod
    def _load_meta(source_file: AuthorizedSourceFile) -> dict[str, JsonValue] | None:
        """Read the sibling ``<stem>.meta.json`` once, tolerating its absence."""
        meta_path = source_file.path.with_name(
            f"{source_file.path.stem}.meta.json"
        )
        try:
            raw = meta_path.read_bytes()
        except (FileNotFoundError, PermissionError, IsADirectoryError):
            return None
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            _log.debug("rivet_meta_unparseable file=%s", meta_path)
            return None
        return value if isinstance(value, dict) else None


def create_tianshu_adapter(
    grant: AuthorizedSourceRoot,
    *,
    source_version: str | None = None,
) -> RivetSourceAdapter:
    return RivetSourceAdapter(
        descriptor=TIANSHU_DESCRIPTOR,
        grant=grant,
        parser=parse_rivet_record,
        source_version=source_version,
        file_predicate=lambda source_file: source_file.relative_path.name.endswith(".jsonl")
        and not source_file.relative_path.name.endswith(".meta.json"),
    )


def parse_rivet_record(
    record: dict[str, JsonValue],
    context: SourceRecordContext,
) -> ParsedMessage | ParsedSkip:
    role = first_string(record.get("role"))
    if role not in {"user", "assistant", "tool"}:
        return ParsedSkip(ScanIssueCode.UNKNOWN_RECORD)
    content = record.get("content")
    if not isinstance(content, str) or not content.strip():
        return ParsedSkip(ScanIssueCode.NO_TEXT)

    # Rivet tags host-injected user messages with ``"origin": "hook"`` (a
    # harness preamble, a skill catalogue, a runtime snapshot). They wear the
    # user's role but are not the person's words, so they never reach
    # extraction — same gate codex applies to its internal envelopes.
    if role == "user" and first_string(record.get("origin")) == "hook":
        return ParsedSkip(ScanIssueCode.INJECTED_MACHINE_NOTICE)

    meta = context.header if isinstance(context.header, dict) else {}
    conversation_id = first_string(meta.get("sessionId")) or conversation_from_relative_path(
        context.relative_path
    )
    workspace = first_string(meta.get("cwd"))
    created_at = parse_timestamp(meta.get("createdAt")) or parse_timestamp(meta.get("updatedAt"))

    if role == "tool":
        written_by = WrittenBy.TOOL
        record_type = "rivet.tool"
    elif role == "assistant":
        written_by = WrittenBy.AGENT
        record_type = "rivet.message"
    else:
        written_by = WrittenBy.PERSON
        record_type = "rivet.message"

    raw_meta: dict[str, JsonValue] = {
        key: meta[key] for key in _RIVET_SESSION_PROVENANCE_FIELDS if key in meta
    }
    origin_metadata = OriginMetadata(
        source_version=first_string(meta.get("model")),
        raw_meta=raw_meta,
    )

    return ParsedMessage(
        conversation_id=conversation_id,
        role=role,
        written_by=written_by,
        created_at=created_at,
        verbatim=content,
        workspace=workspace,
        record_type=record_type,
        origin_metadata=origin_metadata,
    )
