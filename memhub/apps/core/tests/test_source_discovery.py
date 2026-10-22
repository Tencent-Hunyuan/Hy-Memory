from one_memory.imports.access import (
    AuthorizedSourceFile,
    AuthorizedSourceRoot,
    SourceAccessErrorCode,
)
from one_memory.imports.adapters import ScanIssueCode
from one_memory.imports.discovery import (
    SourceFileDiscoveryFailure,
    discover_source_files,
)
from one_memory.imports.sources.common import discovery_failure_code


def grant(root, home):
    return AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=home,
    )


def test_discovery_is_sorted_bounded_and_suffix_scoped(tmp_path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "b.jsonl").write_text("{}\n")
    (root / "a.jsonl").write_text("{}\n")
    (root / "ignored.txt").write_text("private")

    events = list(
        discover_source_files(
            grant(root, tmp_path / "home"),
            suffixes=frozenset({".jsonl"}),
            max_targets=1,
        )
    )

    assert len(events) == 2
    assert isinstance(events[0], AuthorizedSourceFile)
    assert events[0].relative_path.as_posix() == "a.jsonl"
    assert isinstance(events[1], SourceFileDiscoveryFailure)
    assert events[1].reason_code is SourceAccessErrorCode.BUDGET_EXCEEDED


def test_discovery_does_not_follow_directory_or_file_symlinks(tmp_path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.jsonl").write_text('{"private":true}\n')
    (root / "directory-link").symlink_to(outside, target_is_directory=True)
    (root / "file-link.jsonl").symlink_to(outside / "private.jsonl")

    events = list(
        discover_source_files(
            grant(root, tmp_path / "home"),
            suffixes=frozenset({".jsonl"}),
            max_targets=10,
        )
    )

    assert len(events) == 1
    assert isinstance(events[0], SourceFileDiscoveryFailure)
    assert "private" not in events[0].file_key


def test_discovery_reports_an_oversized_file_as_file_too_large(tmp_path) -> None:
    """A size-gate refusal is a size problem, not a schema problem.

    The Codex report in #102 shows the access gate refusing a file over the
    per-root limit while the UI said `unsupported_schema`, which sends the user
    looking for a parser that is working fine.
    """
    root = tmp_path / "source"
    root.mkdir()
    (root / "big.jsonl").write_text('{"content":"too big"}\n')
    granted = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "home",
        max_file_bytes=8,
    )

    events = list(
        discover_source_files(
            granted,
            suffixes=frozenset({".jsonl"}),
            max_targets=10,
        )
    )

    assert len(events) == 1
    assert isinstance(events[0], SourceFileDiscoveryFailure)
    assert events[0].reason_code is SourceAccessErrorCode.FILE_TOO_LARGE
    assert discovery_failure_code(events[0]) is ScanIssueCode.FILE_TOO_LARGE
