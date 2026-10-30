"""DeepSeek Harness versions its session file names; every generation must import.

The first format writes `<session>/session.jsonl[.zstd]`; v3 and v4 write
`session.v3.jsonl.zstd` / `session.v4.jsonl.zstd`. Discovery used to match only
the two unversioned names, so every v3/v4 session was invisible to the import
job — `discovered 0` while the files sat on disk. These tests pin the file-name
contract, the header gate that keeps an unknown future version out of the
database, and the upgrade path where a library imported before the fix picks up
the versioned files on its next scan.
"""

import json
from pathlib import Path

import zstandard

from one_memory.imports.adapters import ScanIssueCode
from one_memory.imports.sources.deepseek_harness import (
    DEEPSEEK_HARNESS_DESCRIPTOR,
    create_deepseek_harness_adapter,
)
from tests.test_secondary_source_adapters import (
    _cursors,
    _evidence,
    _evidence_text,
    _grant,
    _scan,
    _skip_codes,
)


def _write_session(directory: Path, name: str, records: list[dict[str, object]]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = "".join(f"{json.dumps(record)}\n" for record in records).encode()
    (directory / name).write_bytes(zstandard.ZstdCompressor().compress(payload))


def _conversation(version: int | None, *, directory: str = "run-a") -> list[dict[str, object]]:
    header: dict[str, object] = {
        "type": "session",
        "version": version,
        "id": directory,
        "cwd": "/private/versioned-project",
    }
    return [
        header,
        {
            "type": "user/message",
            "seq": 1,
            "data": {
                "id": "human",
                "role": "user",
                "source": {"kind": "user"},
                "content": [{"type": "text", "text": "visible human"}],
            },
        },
        {
            "type": "assistant/message",
            "seq": 2,
            "data": {
                "message": {
                    "id": "assistant",
                    "role": "assistant",
                    "content": [{"type": "text", "text": "visible assistant"}],
                }
            },
        },
    ]


def _session_adapter(tmp_path: Path):
    root = tmp_path / "dsh"
    root.mkdir(parents=True, exist_ok=True)
    return root, create_deepseek_harness_adapter(
        _grant(DEEPSEEK_HARNESS_DESCRIPTOR.source_id, root, tmp_path)
    )


def test_versioned_v4_session_file_is_discovered_and_imported(tmp_path: Path) -> None:
    root, adapter = _session_adapter(tmp_path)
    session = root / "run-v4"
    records = _conversation(4, directory="run-v4")
    records.append(
        {
            "type": "tool/result",
            "seq": 3,
            "time": 1793341068763,
            "data": {
                "callId": "call-1",
                "message": {"role": "tool", "content": [{"type": "text", "text": "tool output"}]},
            },
        }
    )
    _write_session(session, "session.v4.jsonl.zstd", records)
    # A rotated backup next to the live file must not be imported as a second copy.
    (session / "session.v4.jsonl.zstd.orig-20261027").write_bytes(
        zstandard.ZstdCompressor().compress(b'{"type":"session","version":4}\n')
    )

    events = _scan(adapter)

    assert _evidence_text(events) == ["visible human", "visible assistant", "tool output"]
    evidence = _evidence(events)
    assert {event.evidence.project for event in evidence} == {"versioned-project"}
    assert {event.evidence.conversation_id for event in evidence} == {"run-v4"}


def test_versioned_v3_session_file_is_discovered_and_imported(tmp_path: Path) -> None:
    root, adapter = _session_adapter(tmp_path)
    _write_session(root / "run-v3", "session.v3.jsonl.zstd", _conversation(3))

    events = _scan(adapter)

    assert _evidence_text(events) == ["visible human", "visible assistant"]


def test_unknown_future_session_version_imports_nothing(tmp_path: Path) -> None:
    root, adapter = _session_adapter(tmp_path)
    records = _conversation(5)
    records.append(
        {
            "type": "user/message",
            "seq": 9,
            "data": {
                "id": "future",
                "role": "user",
                "source": {"kind": "user"},
                "content": [{"type": "text", "text": "future generation payload"}],
            },
        }
    )
    _write_session(root / "run-v5", "session.v5.jsonl.zstd", records)

    events = _scan(adapter)

    assert _evidence(events) == []
    assert ScanIssueCode.UNSUPPORTED_SCHEMA in _skip_codes(events)


def test_upgrade_scan_rediscovers_versioned_files_already_on_disk(tmp_path: Path) -> None:
    """A library imported before this fix must pick up the files on its next scan.

    The first scan sees only the unversioned session that the old predicate
    matched; after the upgrade, the versioned file next to it is discovered by
    the next scan while the already imported session stays deduplicated by the
    cursor it left behind.
    """
    root, adapter = _session_adapter(tmp_path)
    old = root / "run-old"
    _write_session(old, "session.jsonl.zstd", _conversation(0, directory="run-old"))
    first_scan = _scan(adapter)
    assert _evidence_text(first_scan) == ["visible human", "visible assistant"]
    assert _evidence(first_scan)[0].evidence.conversation_id == "run-old"

    versioned = root / "run-v4"
    _write_session(versioned, "session.v4.jsonl.zstd", _conversation(4, directory="run-v4"))
    cursors = _cursors(first_scan)
    assert cursors  # the old session left a cursor behind

    upgrade_scan = _scan(adapter, cursors=cursors)

    assert _evidence_text(upgrade_scan) == ["visible human", "visible assistant"]
    assert {event.evidence.conversation_id for event in _evidence(upgrade_scan)} == {"run-v4"}
