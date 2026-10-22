"""Pure raw-content admission and exact source maps; no grants, writes or HY calls."""

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Literal

from one_memory.embedding_assets import BUILTIN_EMBEDDING_MAX_TOKENS
from one_memory.imports.models import (
    MAX_VERBATIM_CHARACTERS,
    EvidenceContentKind,
    EvidenceRecord,
)
from one_memory.imports.sources.parsing import (
    is_host_generated_user_turn,
    unwrap_host_envelope_with_positions,
)
from one_memory.input_sanitization import sanitize_model_input_with_spans
from one_memory.local_embedding import TokenizedText
from one_memory.memory_processing import _is_hy_input_turn
from one_memory.retrieval.chunking import ChunkingError, OffsetTokenizer, TextChunk, chunk_evidence

MAP_VERSION = "raw-content-map-v1"
PARSER_VERSION = "host-envelope-positions-v1"
SANITIZER_VERSION = "model-input-spans-v1"
MAX_MAPPED_CHUNKS = 8192


class MappedContentError(ValueError):
    """Fixed reason only; never include original or sanitized content."""


@dataclass(frozen=True)
class MappedSegment:
    kind: Literal["copy", "synthetic"]
    start_cp: int
    end_cp: int
    source_start_cp: int | None
    source_end_cp: int | None


@dataclass(frozen=True)
class ExcludedRange:
    start_cp: int
    end_cp: int
    reason: Literal["envelope_or_trim", "sanitizer"]


@dataclass(frozen=True)
class ContentMap:
    map_version: str
    parser_version: str
    sanitizer_version: str
    source_length_cp: int
    body_start_cp: int | None
    body_end_cp: int | None
    sanitized_length_cp: int
    sanitized_sha256: str
    segments: tuple[MappedSegment, ...]
    excluded: tuple[ExcludedRange, ...]

    def canonical_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json().encode()).hexdigest()


@dataclass(frozen=True)
class PreparedMappedContent:
    actual_text: str = field(repr=False)
    metadata: ContentMap
    state: Literal["ready", "no_indexable_content"]


def _validate(content: PreparedMappedContent, original: str | None = None) -> None:
    meta, text = content.metadata, content.actual_text
    invalid = "mapped_invalid_source_map"
    if (
        (meta.map_version, meta.parser_version, meta.sanitizer_version)
        != (MAP_VERSION, PARSER_VERSION, SANITIZER_VERSION)
        or type(meta.source_length_cp) is not int
        or not 0 <= meta.source_length_cp <= MAX_VERBATIM_CHARACTERS
        or type(meta.sanitized_length_cp) is not int
        or meta.sanitized_length_cp != len(text)
        or meta.sanitized_sha256 != hashlib.sha256(text.encode()).hexdigest()
        or type(meta.segments) is not tuple
        or type(meta.excluded) is not tuple
    ):
        raise MappedContentError(invalid)
    body_start, body_end = meta.body_start_cp, meta.body_end_cp
    if body_start is None or body_end is None:
        if body_start is not None or body_end is not None or text or meta.segments:
            raise MappedContentError(invalid)
    elif (
        type(body_start) is not int
        or type(body_end) is not int
        or not 0 <= body_start < body_end <= meta.source_length_cp
    ):
        raise MappedContentError(invalid)
    cursor = source_cursor = 0
    has_content = False
    partition: list[tuple[int, int]] = []
    for span in meta.segments:
        if (
            not isinstance(span, MappedSegment)
            or type(span.start_cp) is not int
            or type(span.end_cp) is not int
            or not cursor == span.start_cp < span.end_cp <= len(text)
        ):
            raise MappedContentError(invalid)
        cursor = span.end_cp
        if span.kind == "synthetic":
            if span.source_start_cp is not None or span.source_end_cp is not None:
                raise MappedContentError(invalid)
            continue
        left, right = span.source_start_cp, span.source_end_cp
        if (
            span.kind != "copy"
            or type(left) is not int
            or type(right) is not int
            or body_start is None
            or body_end is None
            or not body_start <= left < right <= body_end
            or left < source_cursor
            or right - left != span.end_cp - span.start_cp
        ):
            raise MappedContentError(invalid)
        if original is not None and text[span.start_cp : span.end_cp] != original[left:right]:
            raise MappedContentError(invalid)
        has_content |= bool(text[span.start_cp : span.end_cp].strip())
        source_cursor = right
        partition.append((left, right))
    if cursor != len(text):
        raise MappedContentError(invalid)
    excluded_end = 0
    for excluded in meta.excluded:
        if (
            not isinstance(excluded, ExcludedRange)
            or type(excluded.start_cp) is not int
            or type(excluded.end_cp) is not int
            or not excluded_end <= excluded.start_cp < excluded.end_cp <= meta.source_length_cp
            or excluded.reason not in {"envelope_or_trim", "sanitizer"}
        ):
            raise MappedContentError(invalid)
        excluded_end = excluded.end_cp
        partition.append((excluded.start_cp, excluded.end_cp))
    source_cursor = 0
    for left, right in sorted(partition):
        if left != source_cursor:
            raise MappedContentError(invalid)
        source_cursor = right
    if source_cursor != meta.source_length_cp or content.state != (
        "ready" if has_content else "no_indexable_content"
    ):
        raise MappedContentError(invalid)


def prepare_mapped_content(record: EvidenceRecord) -> PreparedMappedContent:
    """Map a qualified body to Evidence.verbatim code points, without modifying it.

    The returned text is transient; only metadata is suitable for an outer manifest.
    That manifest must independently bind Evidence identity/revision/hash and grants.
    """
    if record.content_kind is not EvidenceContentKind.RAW_CONVERSATION or record.role not in {
        "user",
        "assistant",
    }:
        raise MappedContentError("mapped_content_ineligible")
    original = record.verbatim
    if len(original) > MAX_VERBATIM_CHARACTERS:
        raise MappedContentError("mapped_source_limit_exceeded")
    envelope = unwrap_host_envelope_with_positions(original)
    if envelope.status == "ambiguous":
        raise MappedContentError("mapped_ambiguous_envelope")
    body, start, end = envelope.text, envelope.start_cp, envelope.end_cp
    segments: list[MappedSegment] = []
    excluded: list[ExcludedRange] = []
    text = ""
    if body is None:
        if start is not None or end is not None:
            raise MappedContentError("mapped_invalid_envelope")
        if original:
            excluded.append(ExcludedRange(0, len(original), "envelope_or_trim"))
    else:
        if (
            type(start) is not int
            or type(end) is not int
            or not 0 <= start < end <= len(original)
            or original[start:end] != body
        ):
            raise MappedContentError("mapped_invalid_envelope")
        if is_host_generated_user_turn(original) or is_host_generated_user_turn(body):
            raise MappedContentError("mapped_generated_content")
        if not _is_hy_input_turn(record.model_copy(update={"verbatim": body})):
            raise MappedContentError("mapped_body_hygiene_rejected")
        sanitized = sanitize_model_input_with_spans(body)
        text = sanitized.text
        cursor = 0
        for span in sanitized.spans:
            if span.start_cp > cursor:
                segments.append(MappedSegment("synthetic", cursor, span.start_cp, None, None))
            copied = MappedSegment(
                "copy",
                span.start_cp,
                span.end_cp,
                start + span.source_start_cp,
                start + span.source_end_cp,
            )
            # Only source AND output adjacency can make one citation-safe interval.
            if (
                segments
                and segments[-1].kind == "copy"
                and (
                    segments[-1].end_cp == copied.start_cp
                    and segments[-1].source_end_cp == copied.source_start_cp
                )
            ):
                previous = segments.pop()
                copied = MappedSegment(
                    "copy",
                    previous.start_cp,
                    copied.end_cp,
                    previous.source_start_cp,
                    copied.source_end_cp,
                )
            segments.append(copied)
            cursor = span.end_cp
        if cursor < len(text):
            segments.append(MappedSegment("synthetic", cursor, len(text), None, None))
        if start:
            excluded.append(ExcludedRange(0, start, "envelope_or_trim"))
        excluded.extend(
            ExcludedRange(start + left, start + right, "sanitizer")
            for left, right in sanitized.excluded
        )
        if end < len(original):
            excluded.append(ExcludedRange(end, len(original), "envelope_or_trim"))
    metadata = ContentMap(
        MAP_VERSION,
        PARSER_VERSION,
        SANITIZER_VERSION,
        len(original),
        start,
        end,
        len(text),
        hashlib.sha256(text.encode()).hexdigest(),
        tuple(segments),
        tuple(excluded),
    )
    prepared = PreparedMappedContent(
        text,
        metadata,
        "ready"
        if any(
            span.kind == "copy" and text[span.start_cp : span.end_cp].strip() for span in segments
        )
        else "no_indexable_content",
    )
    _validate(prepared, original)
    return prepared


class _BoundedTokenizer:
    """Count successful chunk re-encodings without copying the chunking algorithm."""

    def __init__(self, tokenizer: OffsetTokenizer, remaining: int, max_tokens: int):
        self.tokenizer = tokenizer
        self.remaining = remaining
        self.max_tokens = max_tokens
        self.initial = True

    def tokenize_with_offsets(self, text: str) -> TokenizedText:
        if not self.initial and self.remaining == 0:
            raise MappedContentError("mapped_chunk_limit_exceeded")
        result = self.tokenizer.tokenize_with_offsets(text)
        # chunk_evidence first encodes the whole copy span, then prefix+candidate;
        # an in-budget candidate emits exactly one chunk. Shrink retries do not.
        if (
            not self.initial
            and isinstance(result, TokenizedText)
            and len(result.token_ids) <= self.max_tokens
        ):
            self.remaining -= 1
        self.initial = False
        return result


def chunk_mapped_content(
    content: PreparedMappedContent,
    tokenizer: OffsetTokenizer,
    *,
    prefix: str = "",
    target_tokens: int = 384,
    overlap_tokens: int = 64,
    max_tokens: int = BUILTIN_EMBEDDING_MAX_TOKENS,
    max_chunks: int = MAX_MAPPED_CHUNKS,
) -> tuple[TextChunk, ...]:
    """Chunk each continuous copy independently; never encode synthetic placeholders."""
    if type(max_chunks) is not int or not 1 <= max_chunks <= MAX_MAPPED_CHUNKS:
        raise MappedContentError("mapped_invalid_chunk_limit")
    _validate(content)
    # Reuse budget validation even for an explicitly nonindexable record.
    chunk_evidence(
        "",
        tokenizer,
        prefix=prefix,
        target_tokens=target_tokens,
        overlap_tokens=overlap_tokens,
        max_tokens=max_tokens,
    )
    chunks: list[TextChunk] = []
    for span in content.metadata.segments:
        if span.kind != "copy":
            continue
        text = content.actual_text[span.start_cp : span.end_cp]
        if not text.strip():
            continue
        if len(chunks) == max_chunks:
            raise MappedContentError("mapped_chunk_limit_exceeded")
        try:
            pieces = chunk_evidence(
                text,
                _BoundedTokenizer(tokenizer, max_chunks - len(chunks), max_tokens),
                prefix=prefix,
                target_tokens=target_tokens,
                overlap_tokens=overlap_tokens,
                max_tokens=max_tokens,
            )
        except ChunkingError as error:
            if isinstance(error.__cause__, MappedContentError):
                raise error.__cause__ from None
            raise
        if len(chunks) + len(pieces) > max_chunks:
            raise MappedContentError("mapped_chunk_limit_exceeded")
        assert span.source_start_cp is not None  # Validated copy span.
        ordinal = len(chunks)
        chunks.extend(
            TextChunk(
                ordinal + i,
                span.source_start_cp + piece.start_cp,
                span.source_start_cp + piece.end_cp,
                piece.token_count,
                piece.text,
            )
            for i, piece in enumerate(pieces)
        )
    return tuple(chunks)
