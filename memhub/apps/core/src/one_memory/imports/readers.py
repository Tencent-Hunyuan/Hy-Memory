import hashlib
import io
import json
import re
from collections.abc import Generator, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import BinaryIO

import zstandard
from pydantic import JsonValue

from one_memory.imports.access import AuthorizedSourceFile
from one_memory.imports.sqlite_access import open_sqlite_readonly

MAX_JSONL_RECORD_BYTES = 10 * 1024 * 1024
#: The import's own ceiling on one source file, deliberately far below the
#: access layer's per-root limit (`DEFAULT_MAX_SOURCE_FILE_BYTES`, 2GB, and
#: 64GB for cursor). Those answer "may this root be read at all"; this answers
#: "is one file small enough to finish importing in a reasonable pass". A
#: 348MB file passes the root check and then holds the import open with no
#: heartbeat, which is what killed core on the 3924c machine. Named
#: separately so the two are never confused for one another.
DEFAULT_IMPORT_MAX_FILE_BYTES = 32 * 1024 * 1024
#: How many records one pass reads from a zstd stream when a budget applies.
#: Sized so a pass stays well inside the byte budget for ordinary transcript
#: records. A pass resumes at the frame boundary recorded in its checkpoint, so
#: this bounds how much *new* content one pass takes, not how much the file
#: holds.
_ZSTD_RECORD_BUDGET = 20_000
#: Every zstd frame starts with this. A `.jsonl.zstd` file is a sequence of
#: them, one per flush, so frame boundaries double as resume points.
ZSTD_FRAME_MAGIC = b"\x28\xb5\x2f\xfd"
SQL_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class RawRecord:
    checkpoint: str
    value: dict[str, JsonValue]
    checkpoint_anchor: str | None = None


class RawReadFailureCode(StrEnum):
    INVALID_JSON = "invalid_json"
    RECORD_NOT_OBJECT = "record_not_object"
    RECORD_TOO_LARGE = "record_too_large"


@dataclass(frozen=True)
class RawReadFailure:
    checkpoint: str
    reason_code: RawReadFailureCode
    checkpoint_anchor: str | None = None


type RawReadEvent = RawRecord | RawReadFailure


def read_jsonl(
    source_file: AuthorizedSourceFile,
    *,
    checkpoint: str | None = None,
    max_record_bytes: int = MAX_JSONL_RECORD_BYTES,
    max_bytes: int | None = None,
    immutable: bool = False,
) -> Generator[RawReadEvent, None, None]:
    """Read forward from `checkpoint`, optionally stopping after `max_bytes`.

    `max_bytes` bounds one pass rather than skipping the file: the generator
    stops on the record that crosses the budget and the caller keeps the last
    checkpoint, so the next pass resumes where this one stopped. A 500MB
    transcript therefore imports across several passes instead of either
    holding the import open for the whole file or being dropped entirely.
    """
    # Only a caller with a frozen extent may treat EOF as a record boundary.
    if max_record_bytes < 1:
        raise ValueError("max_record_bytes must be positive")
    if max_bytes is not None and max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    offset = _parse_checkpoint(checkpoint, "byte")
    with source_file.open_binary() as source:
        _seek_jsonl_checkpoint(source, offset, max_record_bytes=max_record_bytes)
        while line := source.readline(max_record_bytes + 1):
            next_offset = source.tell()
            next_checkpoint = f"byte:{next_offset}"
            if max_bytes is not None and next_offset - offset > max_bytes:
                # Stop on the record that crosses the budget. The cursor is
                # already at this record's end, so the next pass resumes after
                # it rather than re-reading it.
                return
            if not line.endswith(b"\n"):
                if len(line) <= max_record_bytes:
                    if immutable:
                        yield _decode_json_record(line, next_checkpoint)
                    return
                terminated = _discard_until_newline(source, max_record_bytes=max_record_bytes)
                if not terminated and not immutable:
                    return
                next_checkpoint = f"byte:{source.tell()}"
                yield RawReadFailure(
                    next_checkpoint,
                    RawReadFailureCode.RECORD_TOO_LARGE,
                )
                continue
            if len(line) > max_record_bytes:
                yield RawReadFailure(
                    next_checkpoint,
                    RawReadFailureCode.RECORD_TOO_LARGE,
                )
                continue
            yield _decode_json_record(line, next_checkpoint)


def read_zstd_jsonl(
    source_file: AuthorizedSourceFile,
    *,
    checkpoint: str | None = None,
    max_record_bytes: int = MAX_JSONL_RECORD_BYTES,
    max_bytes: int | None = None,
    immutable: bool = False,
) -> Generator[RawReadEvent, None, None]:
    """Use immutable EOF semantics only for a caller-owned frozen input.

    The checkpoint carries the compressed offset of the frame that follows the
    last record read, so a resumed pass seeks there instead of decompressing
    the whole prefix. Without it a growing file is re-decompressed from the top
    on every pass, which is what made a 19 MB session hold a core busy: the
    file never reached `read_to_end` because one pass could not finish it, and
    every later pass paid the full prefix again.
    """
    if max_record_bytes < 1:
        raise ValueError("max_record_bytes must be positive")
    if max_bytes is not None and max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    offset, records_before_frame, records_done = _parse_zstd_checkpoint(checkpoint)
    # Bound how much *new* content one pass takes. The resume point is a frame
    # boundary, so this no longer also bounds how much the file costs to reach.
    max_records = None if max_bytes is None else _ZSTD_RECORD_BUDGET
    index = records_before_frame
    decompressor = zstandard.ZstdDecompressor()
    # A record can straddle a frame boundary, because Harness writes one frame
    # per flush and a flush can land mid-record. `carry` holds the bytes of a
    # record that began in an earlier frame, and `carry_origin` remembers where
    # it began so its checkpoint still names a frame a resume can seek to.
    carry = b""
    carry_origin: tuple[int, int] | None = None
    discarding = False
    with source_file.open_binary() as compressed:
        if offset:
            compressed.seek(offset)
        while True:
            frame_start = compressed.tell()
            base = index
            try:
                frame = _read_zstd_frame(compressed)
            except ValueError:
                # A torn frame at the tail: nothing after it can be trusted.
                return
            if frame is None:
                # A clean EOF. Bytes left over are a record whose newline never
                # arrived, which counts as a record only for a frozen input.
                if immutable and carry and len(carry) <= max_record_bytes:
                    index += 1
                    if index > records_done and carry_origin is not None:
                        yield _decode_json_record(
                            carry, f"frame:{carry_origin[0]}:{carry_origin[1]}:{index}"
                        )
                return
            # Every record this frame yields is named by the start of the frame
            # it began in and the count before that frame, so a resume re-reads
            # that frame and skips exactly the records already produced. A frame
            # that inherits a straddling record keeps the earlier origin: a
            # resume there replays the straddle, which is what makes the record
            # after it nameable at all. Naming the frame's end instead would let
            # a resume jump past the records that share the frame with the one it
            # resumed from. A frame is ~400 bytes, so replaying it is free.
            origin = carry_origin or (frame_start, base)
            try:
                payload = decompressor.decompressobj().decompress(frame)
            except zstandard.ZstdError:
                return
            buffer = io.BytesIO(carry + payload)
            carry = b""
            while line := buffer.readline(max_record_bytes + 1):
                if discarding:
                    if line.endswith(b"\n"):
                        discarding = False
                        carry_origin = None
                        index += 1
                        if index > records_done:
                            yield RawReadFailure(
                                f"frame:{origin[0]}:{origin[1]}:{index}",
                                RawReadFailureCode.RECORD_TOO_LARGE,
                            )
                        origin = (frame_start, base)
                    continue
                if not line.endswith(b"\n"):
                    if len(line) > max_record_bytes:
                        # Longer than the cap and still no newline: keep skipping
                        # to its end, in this frame or a later one, so the records
                        # after it stay reachable.
                        discarding = True
                        carry_origin = origin
                        continue
                    carry = line
                    carry_origin = origin
                    break
                index += 1
                if max_records is not None and index - records_done > max_records:
                    return
                if index <= records_done:
                    # Already delivered. Its bytes are consumed either way, and
                    # the next record starts here rather than in the earlier
                    # frame this one began in.
                    origin = (frame_start, base)
                    continue
                record_checkpoint = f"frame:{origin[0]}:{origin[1]}:{index}"
                origin = (frame_start, base)
                if len(line) > max_record_bytes:
                    yield RawReadFailure(
                        record_checkpoint,
                        RawReadFailureCode.RECORD_TOO_LARGE,
                    )
                    continue
                yield _decode_json_record(line, record_checkpoint)


def read_sqlite_table(
    source_file: AuthorizedSourceFile,
    *,
    table: str,
    id_column: str,
    payload_columns: tuple[str, ...],
    checkpoint: str | None = None,
) -> Iterator[RawRecord]:
    identifiers = (table, id_column, *payload_columns)
    if not all(SQL_IDENTIFIER.fullmatch(identifier) for identifier in identifiers):
        raise ValueError("SQLite table and column names must be safe identifiers")
    last_id = _parse_checkpoint(checkpoint, "row")
    selected = ", ".join(f'"{column}"' for column in (id_column, *payload_columns))
    query = f'SELECT {selected} FROM "{table}" WHERE "{id_column}" > ? ORDER BY "{id_column}"'
    with open_sqlite_readonly(source_file) as connection:
        for row in connection.execute(query, (last_id,)):
            row_id = int(row[id_column])
            value = {column: row[column] for column in payload_columns}
            yield RawRecord(checkpoint=f"row:{row_id}", value=value)


def verify_jsonl_checkpoint(
    source_file: AuthorizedSourceFile,
    *,
    checkpoint: str,
    anchor: str,
    max_record_bytes: int = MAX_JSONL_RECORD_BYTES,
) -> bool:
    offset = _parse_checkpoint(checkpoint, "byte")
    if offset <= 0 or offset > source_file.size:
        return False
    start = max(0, offset - max_record_bytes - 1)
    with source_file.open_binary() as source:
        source.seek(start)
        data = source.read(offset - start)
        following = source.read(2)
    if len(data) != offset - start:
        return False
    if data.endswith(b"\n"):
        _, separator, line = data[:-1].rpartition(b"\n")
        line += b"\n"
    else:
        # A snapshot may finish without a newline. A later live continuation
        # invalidates that boundary; only EOF or a subsequently written line
        # separator lets the anchored extent remain a complete record.
        if following and not (following.startswith(b"\n") or following == b"\r\n"):
            return False
        _, separator, line = data.rpartition(b"\n")
    if start > 0 and not separator:
        return False
    return hashlib.sha256(line).hexdigest() == anchor


def _seek_jsonl_checkpoint(source: BinaryIO, offset: int, *, max_record_bytes: int) -> None:
    source.seek(offset)
    if offset == 0:
        return
    source.seek(offset - 1)
    preceding = source.read(1)
    if preceding == b"\n":
        return
    # The separator appended after an imported frozen EOF terminates that
    # record; it is not a new empty record. Ordinary blank lines still fail.
    following = source.read(2)
    separator_size = 1 if following.startswith(b"\n") else 2 if following == b"\r\n" else 0
    source.seek(offset + separator_size)
    if not following or separator_size:
        return
    # Oversized failures have no anchor to invalidate an old EOF. If the host
    # continued that line, reread from its actual start, never parse the suffix
    # as a separate message. Backward reads remain bounded for long records.
    end = offset
    while end:
        start = max(0, end - max_record_bytes - 1)
        source.seek(start)
        data = source.read(end - start)
        newline = data.rfind(b"\n")
        if newline >= 0:
            source.seek(start + newline + 1)
            return
        end = start
    source.seek(0)


def _parse_checkpoint(checkpoint: str | None, expected_kind: str) -> int:
    if checkpoint is None:
        return 0
    kind, separator, raw_value = checkpoint.partition(":")
    if separator != ":" or kind != expected_kind:
        raise ValueError(f"Expected {expected_kind} checkpoint")
    value = int(raw_value)
    if value < 0:
        raise ValueError("Checkpoint cannot be negative")
    return value


def _parse_zstd_checkpoint(checkpoint: str | None) -> tuple[int, int, int]:
    """Return `(compressed_offset, records_before_frame, records_done)`.

    A zstd frame cannot be resumed mid-way, so the checkpoint names the frame
    that follows the last record read plus two counts: how many records precede
    that frame, and how many records have been produced overall. A resumed pass
    seeks to the frame, skips `records_done - records_before_frame` of its
    records, and continues. Accepts the legacy `record:N`, which carries no
    offset and so still decompresses from the top.
    """
    if checkpoint is None:
        return 0, 0, 0
    kind, separator, raw_value = checkpoint.partition(":")
    if separator != ":":
        raise ValueError("Expected record or frame checkpoint")
    if kind == "record":
        value = int(raw_value)
        if value < 0:
            raise ValueError("Checkpoint cannot be negative")
        return 0, 0, value
    if kind != "frame":
        raise ValueError("Expected record or frame checkpoint")
    offset_raw, separator, rest = raw_value.partition(":")
    if separator != ":":
        raise ValueError("Expected frame:<offset>:<before>:<done>")
    before_raw, separator, done_raw = rest.partition(":")
    if separator != ":":
        raise ValueError("Expected frame:<offset>:<before>:<done>")
    offset = int(offset_raw)
    before = int(before_raw)
    done = int(done_raw)
    if offset < 0 or before < 0 or done < before:
        raise ValueError("Checkpoint cannot be negative")
    return offset, before, done


def _read_zstd_frame(source: BinaryIO) -> bytes | None:
    """One whole zstd frame from `source`, or `None` at a clean EOF.

    Walks the frame header and block headers to learn each block's length, so
    the caller knows exactly where the frame ended. Reading the frame as a unit
    is what lets the checkpoint name a compressed offset: a zstd stream cannot
    be resumed mid-frame, but its frames are independently decodable and sit
    back to back.
    """
    magic = source.read(4)
    if not magic:
        return None
    if magic != ZSTD_FRAME_MAGIC:
        raise ValueError("Not a zstd frame")
    frame = bytearray(magic)
    descriptor = source.read(1)
    if len(descriptor) != 1:
        raise ValueError("Truncated zstd frame header")
    frame += descriptor
    descriptor_byte = descriptor[0]
    content_size_flag = descriptor_byte >> 6
    single_segment = (descriptor_byte >> 5) & 1
    has_checksum = (descriptor_byte >> 2) & 1
    dictionary_id_flag = descriptor_byte & 3
    if not single_segment:
        window = source.read(1)
        if len(window) != 1:
            raise ValueError("Truncated zstd window descriptor")
        frame += window
    header_rest = [0, 1, 2, 4][dictionary_id_flag]
    if content_size_flag == 0:
        header_rest += 1 if single_segment else 0
    else:
        header_rest += [0, 2, 4, 8][content_size_flag]
    if header_rest:
        tail = source.read(header_rest)
        if len(tail) != header_rest:
            raise ValueError("Truncated zstd frame header")
        frame += tail
    while True:
        block_header = source.read(3)
        if len(block_header) != 3:
            raise ValueError("Truncated zstd block header")
        frame += block_header
        value = int.from_bytes(block_header, "little")
        last_block = value & 1
        block_type = (value >> 1) & 3
        block_size = value >> 3
        if block_type == 3:
            raise ValueError("Reserved zstd block type")
        # RLE stores one byte and regenerates `block_size` of it; raw and
        # compressed both store `block_size` bytes.
        payload_size = 1 if block_type == 1 else block_size
        payload = source.read(payload_size)
        if len(payload) != payload_size:
            raise ValueError("Truncated zstd block")
        frame += payload
        if last_block:
            break
    if has_checksum:
        checksum = source.read(4)
        if len(checksum) != 4:
            raise ValueError("Truncated zstd checksum")
        frame += checksum
    return bytes(frame)


def _decode_json_record(line: bytes, checkpoint: str) -> RawReadEvent:
    anchor = hashlib.sha256(line).hexdigest()
    try:
        value = json.loads(line)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return RawReadFailure(
            checkpoint,
            RawReadFailureCode.INVALID_JSON,
            checkpoint_anchor=anchor,
        )
    if not isinstance(value, dict):
        return RawReadFailure(
            checkpoint,
            RawReadFailureCode.RECORD_NOT_OBJECT,
            checkpoint_anchor=anchor,
        )
    return RawRecord(checkpoint, value, checkpoint_anchor=anchor)


def _discard_until_newline(
    source: BinaryIO,
    *,
    max_record_bytes: int,
) -> bool:
    while chunk := source.readline(max_record_bytes + 1):
        if chunk.endswith(b"\n"):
            return True
    return False
