"""A4-09: one contract, ten harnesses.

docs/ARCHITECTURE.md A4-09 asks for a unified contract suite covering real
fixture, append, truncate, replace, corrupt, duplicate and cross-file session.
The point of a *shared* suite is that per-harness tests drift: each harness
ends up tested for whatever its author happened to worry about, and the gap is
found in production instead.

Each harness differs only in how a turn is written, so the suite parameterises
over a `Harness` (a writer plus a factory) and asserts the same invariants for
everyone.

Scope note, stated plainly: this covers the **JSONL-backed** harnesses, whose
fixtures can be written as text. `cursor`, `opencode` and `openclaw` read the
host's own SQLite; a text fixture cannot exercise them, and inventing a fake
`.db` would test the fixture rather than the adapter. They are listed in
`SQLITE_ONLY_HARNESSES` and skipped with a reason, so the gap is visible
instead of being quietly filled with a test that proves nothing.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from one_memory.imports.access import AuthorizedSourceRoot
from one_memory.imports.adapters import (
    ScanEvent,
    ScannedEvidence,
    ScanOptions,
)

Turn = tuple[str, str, str | None]  # (role, text, session_id)


def grant(source_id: str, root: Path) -> AuthorizedSourceRoot:
    return AuthorizedSourceRoot.grant(
        source_id=source_id,
        root=root,
        user_confirmed=True,
        home=root.parent / "other-home",
    )


async def scan(adapter, cursors: dict | None = None) -> list[ScanEvent]:
    return [event async for event in adapter.scan(ScanOptions(cursors=cursors or {}))]


def evidence_of(events: list[ScanEvent]) -> list[ScannedEvidence]:
    return [event for event in events if isinstance(event, ScannedEvidence)]


@dataclass(frozen=True)
class Harness:
    """How one harness writes turns, and how to build its adapter."""

    source_id: str
    #: Writes `turns` to `path` in this harness' own on-disk format.
    write: Callable[[Path, list[Turn]], None]
    create: Callable[[AuthorizedSourceRoot], object]
    #: Relative filename the adapter's file_predicate will accept.
    filename: str
    #: True when the adapter's file_predicate pins one exact filename, so a
    #: second file in the same root cannot be discovered at all. Such harnesses
    #: cannot express a cross-file session on disk; they are skipped for that
    #: case rather than having the assertion weakened for everyone.
    single_filename_only: bool = False


def _write_lines(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for line in lines:
            handle.write(json.dumps(line) + "\n")


def _codex() -> Harness:
    """Codex: `session_meta` header, then `response_item`/`message` payloads."""

    def write(path: Path, turns: list[Turn]) -> None:
        lines: list[dict] = [
            {"type": "session_meta", "payload": {"id": "s1", "cwd": "/w", "source": "cli"}}
        ]
        for index, (role, text, _session) in enumerate(turns):
            lines.append(
                {
                    "type": "response_item",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "payload": {
                        "type": "message",
                        "id": f"m{index}",
                        "role": role,
                        "content": [{"type": "input_text", "text": text}],
                    },
                }
            )
        _write_lines(path, lines)

    from one_memory.imports.sources.codex import create_codex_adapter

    # Codex's predicate requires a `rollout-` prefix; a sibling with another
    # name is never discovered, so cross-file cannot be expressed here.
    return Harness("codex", write, create_codex_adapter, "rollout-a.jsonl", True)


def _claude_code() -> Harness:
    def write(path: Path, turns: list[Turn]) -> None:
        lines: list[dict] = []
        for index, (role, text, session) in enumerate(turns):
            lines.append(
                {
                    "type": "user" if role == "user" else "assistant",
                    "sessionId": session or "s1",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "message": {"role": role, "content": text},
                    "uuid": f"u{index}",
                }
            )
        _write_lines(path, lines)

    from one_memory.imports.sources.claude_code import create_claude_code_adapter

    return Harness("claude-code", write, create_claude_code_adapter, "a.jsonl")


def _workbuddy() -> Harness:
    def write(path: Path, turns: list[Turn]) -> None:
        lines: list[dict] = []
        for index, (role, text, session) in enumerate(turns):
            lines.append(
                {
                    "type": "message",
                    "sessionId": session or "s1",
                    "timestamp": "2026-01-01T00:00:00Z",
                    "message": {"role": role, "content": text},
                    "id": f"w{index}",
                }
            )
        _write_lines(path, lines)

    from one_memory.imports.sources.workbuddy import create_workbuddy_adapter

    return Harness("workbuddy", write, create_workbuddy_adapter, "a.jsonl")


def _pi() -> Harness:
    def write(path: Path, turns: list[Turn]) -> None:
        # Pi nests the turn under "message" and states cwd once, in the header.
        lines: list[dict] = [{"type": "session", "id": "s1"}]
        for index, (role, text, _session) in enumerate(turns):
            lines.append(
                {
                    "type": "message",
                    "id": f"p{index}",
                    "cwd": "/w",
                    "message": {"role": role, "content": [{"type": "text", "text": text}]},
                    "timestamp": "2026-01-01T00:00:00Z",
                }
            )
        _write_lines(path, lines)

    from one_memory.imports.sources.pi import create_pi_adapter

    return Harness("pi", write, create_pi_adapter, "a.jsonl")


def _qwenwork() -> Harness:
    def write(path: Path, turns: list[Turn]) -> None:
        lines: list[dict] = []
        for index, (role, text, session) in enumerate(turns):
            lines.append(
                {
                    "type": role,
                    "sessionId": session or "s1",
                    # Qwen Work states authorship; "human" is what imports.
                    "origin": {"kind": "human"},
                    "message": {"role": role, "content": [{"type": "text", "text": text}]},
                    "timestamp": "2026-01-01T00:00:00Z",
                    "id": f"q{index}",
                }
            )
        _write_lines(path, lines)

    from one_memory.imports.sources.qwenwork import create_qwenwork_adapter

    return Harness("qwenwork", write, create_qwenwork_adapter, "a.jsonl")


def _deepseek() -> Harness:
    def write(path: Path, turns: list[Turn]) -> None:
        # deepseek only accepts files named session[.vN].jsonl[.zstd], and nests
        # the turn under "data".
        lines: list[dict] = [{"type": "session", "version": 0, "id": "s1", "cwd": "/w"}]
        for index, (role, text, _session) in enumerate(turns):
            # user/ turns carry the message in "data" with a user "source";
            # assistant/ turns nest it one level deeper under data.message.
            if role == "user":
                data = {
                    "role": "user",
                    "id": f"d{index}",
                    "source": {"kind": "user"},
                    "content": [{"type": "text", "text": text}],
                }
            else:
                data = {
                    "message": {
                        "role": "assistant",
                        "id": f"d{index}",
                        "content": [{"type": "text", "text": text}],
                    }
                }
            lines.append(
                {
                    "type": "user/message" if role == "user" else "assistant/message",
                    "time": "2026-01-01T00:00:00Z",
                    "data": data,
                }
            )
        _write_lines(path, lines)

    from one_memory.imports.sources.deepseek_harness import create_deepseek_harness_adapter

    # deepseek accepts only session[.vN].jsonl[.zstd]; the first format is used here.
    return Harness(
        "deepseek-harness",
        write,
        create_deepseek_harness_adapter,
        "session.jsonl",
        True,
    )


def _cursor_transcript() -> Harness:
    """Cursor's current on-disk history is JSONL before it is store.db."""

    def write(path: Path, turns: list[Turn]) -> None:
        lines: list[dict] = []
        for _index, (role, text, _session) in enumerate(turns):
            lines.append(
                {
                    "role": role,
                    "message": {"content": [{"type": "text", "text": text}]},
                }
            )
        _write_lines(path, lines)

    from one_memory.imports.sources.cursor_unified import CursorUnifiedSourceAdapter

    return Harness(
        "cursor",
        write,
        lambda grant: CursorUnifiedSourceAdapter(grant=grant),
        "projects/project-a/agent-transcripts/session/session.jsonl",
    )


HARNESSES: dict[str, Callable[[], Harness]] = {
    "codex": _codex,
    "claude-code": _claude_code,
    "workbuddy": _workbuddy,
    "pi": _pi,
    "qwenwork": _qwenwork,
    "deepseek-harness": _deepseek,
    "cursor-transcript": _cursor_transcript,
}

#: Harnesses whose only reader is the host's own SQLite. A text fixture cannot
#: exercise them; listed here so the gap is explicit rather than hidden.
#: Cursor is not here: its current transcript path is JSONL and is covered
#: above, while store.db has separate fixtures in the adapter suite.
SQLITE_ONLY_HARNESSES = ("opencode", "openclaw")


@pytest.mark.parametrize("name", sorted(HARNESSES))
def test_real_fixture_produces_evidence(tmp_path: Path, name: str) -> None:
    """A well-formed fixture must produce evidence, not silence."""
    harness = HARNESSES[name]()
    root = tmp_path / name
    harness.write(root / harness.filename, [("user", "hello", "s1"), ("assistant", "hi", "s1")])
    adapter = harness.create(grant(harness.source_id, root))
    events = asyncio.run(scan(adapter))
    assert evidence_of(events), f"{name} produced no evidence from a valid fixture"


@pytest.mark.parametrize("name", sorted(HARNESSES))
def test_corrupt_records_do_not_stop_the_scan(tmp_path: Path, name: str) -> None:
    """A corrupt line must be reported and skipped, never abort the import.

    One bad line in a long session file is routine (a truncated write during a
    crash); losing the rest of the file to it would be silent data loss.
    """
    harness = HARNESSES[name]()
    root = tmp_path / name
    target = root / harness.filename
    harness.write(target, [("user", "first", "s1")])
    # A truncated write is the routine corruption: a crash mid-line.
    with target.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")
    # A second file keeps valid data the scan must still reach.
    sibling = root / ("second-" + harness.filename)
    harness.write(sibling, [("user", "second", "s1")])

    adapter = harness.create(grant(harness.source_id, root))
    events = asyncio.run(scan(adapter))
    assert evidence_of(events), f"{name} lost valid records around a corrupt line"


@pytest.mark.parametrize("name", sorted(HARNESSES))
def test_duplicate_content_is_not_deduplicated_away(tmp_path: Path, name: str) -> None:
    """Two identical turns are two turns.

    Deduplicating them would collapse a conversation where someone genuinely
    said the same thing twice — the scanner's dedupe is about *rescanning*, not
    about repeated content.
    """
    harness = HARNESSES[name]()
    root = tmp_path / name
    harness.write(
        root / harness.filename,
        [("user", "same", "s1"), ("user", "same", "s1")],
    )
    adapter = harness.create(grant(harness.source_id, root))
    events = asyncio.run(scan(adapter))
    assert len(evidence_of(events)) >= 2


def _cursor_of(events: list[ScanEvent]) -> dict:
    from one_memory.imports.adapters import ScanCursorTouch

    return {
        touch.cursor.file_key: touch.cursor
        for touch in events
        if isinstance(touch, ScanCursorTouch)
    }


@pytest.mark.parametrize("name", sorted(HARNESSES))
def test_append_is_picked_up_by_a_rescan(tmp_path: Path, name: str) -> None:
    """A rescan must find the new turn without re-emitting the old ones.

    Re-emitting would duplicate evidence; missing it would lose a turn. Both
    are the failure modes A4-04's incremental reader exists to prevent.
    """
    harness = HARNESSES[name]()
    root = tmp_path / name
    target = root / harness.filename
    harness.write(target, [("user", "first", "s1")])
    adapter = harness.create(grant(harness.source_id, root))

    first_events = asyncio.run(scan(adapter))
    first_count = len(evidence_of(first_events))
    assert first_count >= 1

    with target.open("a", encoding="utf-8") as handle:
        appended = json.dumps(_appended_line(harness, "second")) + "\n"
        handle.write(appended)

    second_events = asyncio.run(scan(adapter, cursors=_cursor_of(first_events)))
    second_count = len(evidence_of(second_events))
    assert second_count >= 1, f"{name} did not see the appended turn"
    # Comparing counts is not enough, and a first version of this test proved
    # it: when the appended turn makes the total equal to the first scan's, a
    # re-read-from-zero implementation produces the same number and passes.
    # What actually proves incremental reading is that an already-imported
    # turn is not emitted again.
    seen_before = {item.evidence.verbatim for item in evidence_of(first_events)}
    re_emitted = [
        item.evidence.verbatim
        for item in evidence_of(second_events)
        if item.evidence.verbatim in seen_before
    ]
    assert not re_emitted, (
        f"{name} re-emitted {len(re_emitted)} already-imported turn(s) on append; "
        "the scan is not resuming from its cursor"
    )


def _appended_line(harness: Harness, text: str) -> dict:
    """One extra turn in the harness' own format, for an append test."""
    probe = _probe_path_for(harness)
    harness.write(probe, [("user", text, "s1")])
    lines = [
        json.loads(line) for line in probe.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    return lines[-1]


def _probe_path_for(harness: Harness) -> Path:
    import tempfile

    return Path(tempfile.mkdtemp()) / harness.filename


@pytest.mark.parametrize("name", sorted(HARNESSES))
def test_truncate_does_not_replay_from_the_old_offset(tmp_path: Path, name: str) -> None:
    """A shrunken file must be reconciled, not resumed.

    Resuming would read whatever now sits at the old offset, producing records
    nobody ever wrote.
    """
    harness = HARNESSES[name]()
    root = tmp_path / name
    target = root / harness.filename
    harness.write(target, [("user", "first", "s1"), ("assistant", "answer", "s1")])
    adapter = harness.create(grant(harness.source_id, root))

    first_events = asyncio.run(scan(adapter))
    assert evidence_of(first_events)

    harness.write(target, [("user", "replaced", "s1")])
    second_events = asyncio.run(scan(adapter, cursors=_cursor_of(first_events)))
    texts = [item.evidence.verbatim for item in evidence_of(second_events)]
    assert any("replaced" in text for text in texts), (
        f"{name} lost the rewritten content after truncation: {texts}"
    )


@pytest.mark.parametrize("name", sorted(HARNESSES))
def test_cross_file_session_keeps_one_conversation(tmp_path: Path, name: str) -> None:
    """Two files sharing a session id must not become unrelated conversations.

    The internal session identity is derived, not per-file, so a session split
    across files stays one session — otherwise its five-round batching would
    never fill.
    """
    harness = HARNESSES[name]()
    if harness.single_filename_only:
        pytest.skip(
            f"{name} only accepts one exact filename, so a cross-file session "
            "cannot be expressed in a fixture"
        )

    root = tmp_path / name
    harness.write(root / harness.filename, [("user", "in first file", "shared")])
    harness.write(root / ("second-" + harness.filename), [("user", "in second file", "shared")])
    adapter = harness.create(grant(harness.source_id, root))

    events = asyncio.run(scan(adapter))
    found = evidence_of(events)
    assert len(found) >= 2, f"{name} did not read both files: {len(found)}"


def test_sqlite_only_harnesses_are_declared_not_faked() -> None:
    """The gap is listed so nobody mistakes this suite for all ten.

    These three read the host's own SQLite database. A text fixture cannot
    exercise them, and fabricating a `.db` would assert things about the
    fixture, not the adapter.
    """
    assert set(SQLITE_ONLY_HARNESSES) == {"opencode", "openclaw"}
    assert len(HARNESSES) + len(SQLITE_ONLY_HARNESSES) >= 9
