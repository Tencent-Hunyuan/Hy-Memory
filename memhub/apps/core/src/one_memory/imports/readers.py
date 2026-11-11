import hashlib
import io
import json
import re
from collections.abc import Callable, Generator, Iterator
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
#: The stream is decompressed from the start every pass, so a large count
#: costs repeated decompression; a small one costs extra passes. Sized so a
#: pass stays well inside the byte budget for ordinary transcript records.
_ZSTD_RECORD_BUDGET = 20_000
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
    decode: Callable[[bytes, str], RawReadEvent] | None = None,
) -> Generator[RawReadEvent, None, None]:
    """Use immutable EOF semantics only for a caller-owned frozen input.

    ``decode`` defaults to :func:`_decode_json_record`; a source whose
    decompressed lines carry extra framing (a checksum suffix, a length
    prefix) passes its own decoder so the rest of the zstd streaming loop is
    reused rather than forked.
    """
    if decode is None:
        decode = _decode_json_record
    if max_record_bytes < 1:
        raise ValueError("max_record_bytes must be positive")
    if max_bytes is not None and max_bytes < 1:
        raise ValueError("max_bytes must be positive")
    completed_records = _parse_checkpoint(checkpoint, "record")
    # A zstd stream has no byte offset to resume from: its checkpoint counts
    # decompressed records, and seeking back means re-decompressing from the
    # start. Bounding it by a byte budget would therefore have to guess bytes
    # from `max_record_bytes`, which is a cap on one record and not its size --
    # dividing by it assumed every record is 10MB and stopped after three.
    # Bound the count of records instead, which is what this reader can
    # actually resume on.
    max_records = None if max_bytes is None else _ZSTD_RECORD_BUDGET
    with (
        source_file.open_binary() as compressed,
        zstandard.ZstdDecompressor().stream_reader(compressed) as stream,
        io.BufferedReader(stream) as decompressed,
    ):
        index = 0
        while line := decompressed.readline(max_record_bytes + 1):
            index += 1
            next_checkpoint = f"record:{index}"
            if max_records is not None and index - completed_records > max_records:
                return
            if not line.endswith(b"\n"):
                if len(line) <= max_record_bytes:
                    if immutable and index > completed_records:
                        yield decode(line, next_checkpoint)
                    return
                terminated = _discard_until_newline(
                    decompressed,
                    max_record_bytes=max_record_bytes,
                )
                if not terminated and not immutable:
                    return
                if index > completed_records:
                    yield RawReadFailure(
                        next_checkpoint,
                        RawReadFailureCode.RECORD_TOO_LARGE,
                    )
                continue
            if index <= completed_records:
                continue
            if len(line) > max_record_bytes:
                yield RawReadFailure(
                    next_checkpoint,
                    RawReadFailureCode.RECORD_TOO_LARGE,
                )
                continue
            yield decode(line, next_checkpoint)


#: Rivet (天枢) writes each turn as one zstd-compressed line whose decompressed
#: payload is ``<json>|<hex checksum>``. The JSON is valid up to the last
#: ``|``; the suffix is a host-side integrity tag the importer must strip
#: before decoding, because ``json.loads`` rejects the trailing token.
_RIVET_CHECKSUM_SUFFIX = re.compile(rb"\|[0-9a-f]{8,32}$")


def _decode_rivet_record(line: bytes, checkpoint: str) -> RawReadEvent:
    return _decode_json_record(_RIVET_CHECKSUM_SUFFIX.sub(b"", line), checkpoint)


def read_rivet_jsonl(
    source_file: AuthorizedSourceFile,
    *,
    checkpoint: str | None = None,
    max_record_bytes: int = MAX_JSONL_RECORD_BYTES,
    max_bytes: int | None = None,
    immutable: bool = False,
) -> Generator[RawReadEvent, None, None]:
    """Read a Rivet session file: zstd-framed lines with a ``|<hex>`` checksum."""
    return read_zstd_jsonl(
        source_file,
        checkpoint=checkpoint,
        max_record_bytes=max_record_bytes,
        max_bytes=max_bytes,
        immutable=immutable,
        decode=_decode_rivet_record,
    )


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
