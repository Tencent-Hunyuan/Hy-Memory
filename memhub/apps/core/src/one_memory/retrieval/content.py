"""Shared qualification for identity-mapped raw indexing and candidate reads."""

from collections.abc import Sequence

from one_memory.conversation_processing import _is_hy_input_turn
from one_memory.imports.models import EvidenceContentKind, EvidenceRecord
from one_memory.imports.sources.parsing import is_host_generated_user_turn, strip_host_wrappers
from one_memory.input_sanitization import sanitize_model_input
from one_memory.retrieval.chunking import TextChunk
from one_memory.retrieval.mapped_content import PreparedMappedContent, prepare_mapped_content


def identity_content_ready(record: EvidenceRecord) -> bool:
    # Reuse pure existing hygiene checks, without invoking HY. Nonidentity
    # cleaning requires a source map and remains explicitly uncovered for now.
    return (
        record.content_kind is EvidenceContentKind.RAW_CONVERSATION
        and record.role in {"user", "assistant"}
        and _is_hy_input_turn(record)
        and not is_host_generated_user_turn(record.verbatim)
        and strip_host_wrappers(record.verbatim) == record.verbatim.strip()
        and sanitize_model_input(record.verbatim) == record.verbatim
    )


def index_content(
    record: EvidenceRecord, *, normalizer_version: int, chunker_version: int
) -> PreparedMappedContent | None:
    """Generation-owned content semantics, never silently upgrade identity generations."""
    if (normalizer_version, chunker_version) == (1, 1):
        if not identity_content_ready(record):
            raise ValueError("content_mapping_required")
        return None
    if (normalizer_version, chunker_version) == (2, 2):
        return prepare_mapped_content(record)
    raise ValueError("unsupported_content_generation")


def validate_chunk_coverage(
    record: EvidenceRecord, chunks: Sequence[TextChunk], prepared: PreparedMappedContent | None
) -> None:
    """Verify exact slices and complete citation-safe coverage without a tokenizer call."""
    if len(chunks) > 8192 or (prepared is None and not chunks):
        raise ValueError("invalid_chunk_manifest")
    if prepared is None:
        ranges = [(0, len(record.verbatim))]
    else:
        ranges = []
        for span in prepared.metadata.segments:
            if span.kind == "copy":
                assert span.source_start_cp is not None and span.source_end_cp is not None
                ranges.append((span.source_start_cp, span.source_end_cp))
    range_index = 0
    covered_end = ranges[0][0] if ranges else 0
    previous_end = -1
    for index, chunk in enumerate(chunks):
        if not isinstance(chunk, TextChunk) or any(
            type(value) is not int
            for value in (chunk.ordinal, chunk.start_cp, chunk.end_cp, chunk.token_count)
        ):
            raise ValueError("invalid_chunk_manifest")
        while range_index < len(ranges) and chunk.start_cp >= ranges[range_index][1]:
            if record.verbatim[covered_end : ranges[range_index][1]].strip():
                raise ValueError("incomplete_chunk_manifest")
            range_index += 1
            covered_end = ranges[range_index][0] if range_index < len(ranges) else 0
        if range_index >= len(ranges):
            raise ValueError("invalid_chunk_manifest")
        left, right = ranges[range_index]
        if (
            chunk.ordinal != index
            or not left <= chunk.start_cp < chunk.end_cp <= right
            or chunk.end_cp <= previous_end
            or not 1 <= chunk.token_count <= 512
            or record.verbatim[chunk.start_cp : chunk.end_cp] != chunk.text
            or (
                chunk.start_cp > covered_end
                and (prepared is None or record.verbatim[covered_end : chunk.start_cp].strip())
            )
        ):
            raise ValueError("invalid_chunk_manifest")
        covered_end = previous_end = chunk.end_cp
    while range_index < len(ranges):
        if prepared is None:
            if covered_end != ranges[range_index][1]:
                raise ValueError("incomplete_chunk_manifest")
        elif record.verbatim[covered_end : ranges[range_index][1]].strip():
            raise ValueError("incomplete_chunk_manifest")
        range_index += 1
        covered_end = ranges[range_index][0] if range_index < len(ranges) else 0
    if prepared is not None and (not chunks) != (prepared.state == "no_indexable_content"):
        raise ValueError("invalid_chunk_manifest")
