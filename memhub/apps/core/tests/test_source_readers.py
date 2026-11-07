import json
import sqlite3

import zstandard

from one_memory.imports.access import AuthorizedSourceRoot
from one_memory.imports.readers import (
    RawReadFailure,
    RawRecord,
    read_jsonl,
    read_sqlite_table,
    read_zstd_jsonl,
    verify_jsonl_checkpoint,
)


def authorized(path):
    grant = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=path.parent,
        user_confirmed=True,
        home=path.parent / "other-home",
    )
    return grant.authorize_file(path)


def test_jsonl_is_resumable_and_does_not_advance_over_an_incomplete_tail(
    tmp_path,
) -> None:
    source = tmp_path / "history.jsonl"
    source.write_bytes(b'{"id":1}\nnot-json\n{"id":')

    events = list(read_jsonl(authorized(source)))

    assert isinstance(events[0], RawRecord)
    assert isinstance(events[1], RawReadFailure)
    assert len(events) == 2
    checkpoint = events[-1].checkpoint

    with source.open("ab") as output:
        output.write(b"3}\n")
    resumed = list(read_jsonl(authorized(source), checkpoint=checkpoint))

    assert [event.value["id"] for event in resumed if isinstance(event, RawRecord)] == [3]


def test_sqlite_reader_uses_monotonic_row_checkpoint(tmp_path) -> None:
    source = tmp_path / "history.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY, body TEXT)")
    connection.executemany(
        "INSERT INTO messages (body) VALUES (?)",
        [("one",), ("two",), ("three",)],
    )
    connection.commit()
    connection.close()

    records = list(
        read_sqlite_table(
            authorized(source),
            table="messages",
            id_column="id",
            payload_columns=("body",),
            checkpoint="row:1",
        )
    )

    assert [record.value["body"] for record in records] == ["two", "three"]
    assert records[-1].checkpoint == "row:3"


def test_zstd_jsonl_reader_resumes_by_completed_record(tmp_path) -> None:
    source = tmp_path / "history.jsonl.zst"
    content = b"".join(json.dumps({"id": item}).encode() + b"\n" for item in range(1, 4))
    source.write_bytes(zstandard.ZstdCompressor().compress(content))

    records = list(read_zstd_jsonl(authorized(source), checkpoint="record:1"))

    assert [record.value["id"] for record in records if isinstance(record, RawRecord)] == [
        2,
        3,
    ]


def test_jsonl_checkpoint_anchor_rejects_in_place_rewrite(tmp_path) -> None:
    source = tmp_path / "history.jsonl"
    source.write_bytes(b'{"id":1}\n')
    first = list(read_jsonl(authorized(source)))[0]
    assert first.checkpoint_anchor
    source.write_bytes(b'{"id":2}\n')

    assert not verify_jsonl_checkpoint(
        authorized(source),
        checkpoint=first.checkpoint,
        anchor=first.checkpoint_anchor,
    )


def test_zstd_reader_bounds_a_single_expanded_record(tmp_path) -> None:
    source = tmp_path / "history.jsonl.zst"
    source.write_bytes(zstandard.ZstdCompressor().compress(b'{"body":"' + b"x" * 4096 + b'"}\n'))

    events = list(read_zstd_jsonl(authorized(source), max_record_bytes=64))

    assert len(events) == 1
    assert isinstance(events[0], RawReadFailure)


def test_zstd_reader_reads_past_the_first_frame(tmp_path) -> None:
    """DeepSeek Harness writes one zstd frame per durable append batch.

    Its own package reference describes the artifact as "a standard
    concatenation of independent Zstandard frames: one checksummed frame
    containing only the header line, then one checksummed frame per durable
    append batch". A reader that stops at the first frame boundary therefore
    sees the header and nothing else: an import that reports success and
    contributes no turns. Every other zstd test here compresses one payload
    into a single frame, so none of them would notice.
    """
    compressor = zstandard.ZstdCompressor(write_checksum=True)
    source = tmp_path / "session.jsonl.zstd"
    source.write_bytes(
        b"".join(
            compressor.compress(json.dumps(record).encode() + b"\n")
            for record in (
                {"type": "session/header"},
                {"type": "user", "content": "first batch"},
                {"type": "user", "content": "second batch"},
            )
        )
    )

    records = [
        event for event in read_zstd_jsonl(authorized(source)) if isinstance(event, RawRecord)
    ]

    assert [record.value.get("content") for record in records] == [
        None,
        "first batch",
        "second batch",
    ]


def test_zstd_reader_resumes_across_frames(tmp_path) -> None:
    """Resuming must count records, not frames.

    Harness appends a frame per batch, so a checkpoint taken mid-session lands
    inside the concatenation. If resume were frame-addressed it would either
    replay or skip whole batches.
    """
    compressor = zstandard.ZstdCompressor(write_checksum=True)
    source = tmp_path / "session.jsonl.zstd"
    source.write_bytes(
        b"".join(
            compressor.compress(json.dumps({"id": item}).encode() + b"\n") for item in range(1, 5)
        )
    )

    records = [
        event.value["id"]
        for event in read_zstd_jsonl(authorized(source), checkpoint="record:2")
        if isinstance(event, RawRecord)
    ]

    assert records == [3, 4]


def test_zstd_reader_checkpoint_names_the_frame_that_holds_the_record(tmp_path) -> None:
    """A resume must seek to the recorded frame instead of decompressing the prefix.

    Without an offset a growing session is re-decompressed from the top on
    every pass, and one pass that cannot finish the file leaves `read_to_end`
    false forever: the file is then re-read on every scan.
    """
    compressor = zstandard.ZstdCompressor(write_checksum=True)
    source = tmp_path / "session.jsonl.zstd"
    frames = [
        compressor.compress(json.dumps({"id": item}).encode() + b"\n") for item in range(1, 6)
    ]
    source.write_bytes(b"".join(frames))

    records = [
        event for event in read_zstd_jsonl(authorized(source)) if isinstance(event, RawRecord)
    ]
    assert [event.value["id"] for event in records] == [1, 2, 3, 4, 5]

    checkpoint = records[2].checkpoint
    kind, _, rest = checkpoint.partition(":")
    assert kind == "frame"
    offset_raw, _, counts = rest.partition(":")
    # Record 3 lives in the third frame, so the offset is the sum of the first two.
    assert int(offset_raw) == len(frames[0]) + len(frames[1])
    before_raw, _, done_raw = counts.partition(":")
    assert int(before_raw) == 2
    assert int(done_raw) == 3

    resumed = [
        event.value["id"]
        for event in read_zstd_jsonl(authorized(source), checkpoint=checkpoint)
        if isinstance(event, RawRecord)
    ]
    assert resumed == [4, 5]


def test_zstd_reader_resumes_inside_a_multi_record_frame(tmp_path) -> None:
    """One frame can hold several records; a resume must not skip its siblings.

    Harness writes a frame per append batch, and a batch carries several
    records. Naming the frame's end in the checkpoint would make a resume jump
    past the records that share the frame with the one it resumed from.
    """
    compressor = zstandard.ZstdCompressor(write_checksum=True)
    source = tmp_path / "session.jsonl.zstd"
    first_batch = b"".join(json.dumps({"id": item}).encode() + b"\n" for item in range(1, 4))
    source.write_bytes(
        compressor.compress(first_batch) + compressor.compress(b'{"id":4}\n')
    )

    records = [
        event for event in read_zstd_jsonl(authorized(source)) if isinstance(event, RawRecord)
    ]
    assert [event.value["id"] for event in records] == [1, 2, 3, 4]

    # Record 2 shares its frame with 1 and 3: resuming from it still delivers 3.
    resumed = [
        event.value["id"]
        for event in read_zstd_jsonl(authorized(source), checkpoint=records[1].checkpoint)
        if isinstance(event, RawRecord)
    ]
    assert resumed == [3, 4]


def test_zstd_reader_accepts_a_legacy_record_checkpoint(tmp_path) -> None:
    """Cursors written before the frame offset still resume correctly."""
    compressor = zstandard.ZstdCompressor(write_checksum=True)
    source = tmp_path / "session.jsonl.zstd"
    source.write_bytes(
        b"".join(
            compressor.compress(json.dumps({"id": item}).encode() + b"\n") for item in range(1, 5)
        )
    )

    records = [
        event.value["id"]
        for event in read_zstd_jsonl(authorized(source), checkpoint="record:1")
        if isinstance(event, RawRecord)
    ]

    assert records == [2, 3, 4]
