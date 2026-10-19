from pathlib import Path

from one_memory.imports.access import AuthorizedSourceFile
from one_memory.imports.models import MAX_VERBATIM_CHARACTERS, WrittenBy
from one_memory.imports.sources.common import ParsedMessage, to_evidence


def _source_file(tmp_path: Path) -> AuthorizedSourceFile:
    return AuthorizedSourceFile(
        source_id="claude-code",
        root=tmp_path,
        path=tmp_path / "session.jsonl",
        relative_path=Path("session.jsonl"),
        max_bytes=400_000_000,
        file_identity="identity",
        size=1,
        modified_ns=1,
        changed_ns=1,
    )


def _evidence(tmp_path: Path, verbatim: str):
    return to_evidence(
        source_id="claude-code",
        source_file=_source_file(tmp_path),
        parsed=ParsedMessage(
            conversation_id="conversation",
            message_id="message",
            role="user",
            written_by=WrittenBy.PERSON,
            verbatim=verbatim,
            raw_verbatim=verbatim,
        ),
        ordinal="1",
        source_version=None,
    )


def test_oversized_verbatim_is_truncated_instead_of_rejected(tmp_path):
    oversized = "x" * (MAX_VERBATIM_CHARACTERS + 5)
    evidence = _evidence(tmp_path, oversized)

    assert len(evidence.verbatim) <= MAX_VERBATIM_CHARACTERS
    assert "truncated: original 1000005 chars" in evidence.verbatim
    assert len(evidence.raw_verbatim) <= MAX_VERBATIM_CHARACTERS
    assert "truncated: original 1000005 chars" in evidence.raw_verbatim


def test_normal_verbatim_is_unchanged(tmp_path):
    evidence = _evidence(tmp_path, "a normal message")
    assert evidence.verbatim == "a normal message"
    assert evidence.raw_verbatim == "a normal message"
