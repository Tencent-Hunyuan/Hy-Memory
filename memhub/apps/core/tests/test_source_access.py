import asyncio
import os
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from one_memory.imports import access as access_module
from one_memory.imports.access import (
    AuthorizedSourceRoot,
    SourceAccessError,
    SourceAccessErrorCode,
    _changed_ns,
    opaque_conversation_error_id,
    opaque_workspace_id,
    safe_project_label,
)
from one_memory.imports.adapters import (
    ScanIssueCode,
    SourceCapabilities,
    SourceDescriptor,
    StorageKind,
)
from one_memory.imports.jobs import ImportJobStore, ImportStatus
from one_memory.imports.runner import ImportRunner
from one_memory.imports.sources.common import JsonlSourceAdapter, ParsedSkip, _scan_failure_code
from one_memory.imports.sqlite_access import open_sqlite_readonly
from one_memory.imports.storage import EvidenceStore


def test_source_root_requires_explicit_user_grant(tmp_path) -> None:
    with pytest.raises(SourceAccessError) as captured:
        AuthorizedSourceRoot.grant(
            source_id="fixture",
            root=tmp_path,
            user_confirmed=False,
            home=tmp_path / "other-home",
        )

    assert captured.value.code is SourceAccessErrorCode.GRANT_REQUIRED


def test_sensitive_home_directory_is_rejected_without_requesting_os_access(
    tmp_path,
) -> None:
    home = tmp_path / "home"
    documents = home / "Documents" / "agent"
    documents.mkdir(parents=True)

    with pytest.raises(SourceAccessError) as captured:
        AuthorizedSourceRoot.grant(
            source_id="fixture",
            root=documents,
            user_confirmed=True,
            home=home,
        )

    assert captured.value.code is SourceAccessErrorCode.SENSITIVE_ROOT


def test_authorized_file_cannot_escape_root_through_a_symlink(tmp_path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    secret = tmp_path / "secret.jsonl"
    secret.write_text('{"secret":true}\n', encoding="utf-8")
    (root / "session.jsonl").symlink_to(secret)
    grant = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    )

    with pytest.raises(SourceAccessError) as captured:
        grant.authorize_file(Path("session.jsonl"))

    assert captured.value.code is SourceAccessErrorCode.SYMLINK_NOT_ALLOWED


def test_authorized_file_open_refuses_a_parent_directory_replaced_by_a_symlink(tmp_path) -> None:
    """A listed file must not escape through a parent swap after authorization."""
    root = tmp_path / "source"
    directory = root / "nested"
    directory.mkdir(parents=True)
    source = directory / "history.jsonl"
    source.write_text('{"safe":true}\n', encoding="utf-8")
    grant = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    )
    authorized = grant.authorize_file(Path("nested/history.jsonl"))

    moved = tmp_path / "moved"
    directory.rename(moved)
    (root / "nested").symlink_to(moved, target_is_directory=True)

    with pytest.raises(SourceAccessError) as captured:
        authorized.open_binary()

    assert captured.value.code is SourceAccessErrorCode.SYMLINK_NOT_ALLOWED


def test_authorized_file_open_closes_a_parent_swap_race(tmp_path, monkeypatch) -> None:
    root = tmp_path / "source"
    nested = root / "nested"
    nested.mkdir(parents=True)
    source = nested / "history.jsonl"
    source.write_bytes(b"ESCAPE_CANARY\n")
    moved = tmp_path / "outside"
    authorized = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    ).authorize_file(Path("nested/history.jsonl"))
    real_open = os.open
    swapped = False

    monkeypatch.setattr(
        access_module, "_supports_descriptor_relative_open", lambda: True, raising=False
    )

    def swap_then_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and (path == "nested" or Path(path) == source):
            swapped = True
            nested.rename(moved)
            nested.symlink_to(moved, target_is_directory=True)
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(access_module.os, "open", swap_then_open)
    captured = None
    exposed = None
    try:
        with authorized.open_binary() as handle:
            exposed = handle.read()
    except SourceAccessError as error:
        captured = error

    assert swapped
    assert captured is not None, f"read outside authorized root: {exposed!r}"
    assert captured.code is SourceAccessErrorCode.SYMLINK_NOT_ALLOWED


def test_authorized_file_open_rejects_held_parent_moved_out_of_namespace(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "source"
    nested = root / "nested"
    nested.mkdir(parents=True)
    source = nested / "history.jsonl"
    source.write_bytes(b"safe\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / source.name).write_bytes(b"ESCAPE_CANARY\n")
    authorized = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    ).authorize_file(Path("nested/history.jsonl"))
    real_open = os.open
    swapped = False
    open_calls = 0

    monkeypatch.setattr(
        access_module, "_supports_descriptor_relative_open", lambda: True, raising=False
    )

    def swap_then_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal open_calls, swapped
        open_calls += 1
        if not swapped and open_calls == 3:
            swapped = True
            nested.rename(tmp_path / "moved")
            nested.symlink_to(outside, target_is_directory=True)
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(access_module.os, "open", swap_then_open)
    with pytest.raises(SourceAccessError) as captured:
        authorized.open_binary()
    assert swapped
    assert captured.value.code is SourceAccessErrorCode.SYMLINK_NOT_ALLOWED


def test_retargeted_root_alias_is_rejected_by_open_binary(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "history.jsonl").write_bytes(b"safe\n")
    (second / "history.jsonl").write_bytes(b"other\n")
    alias = tmp_path / "chosen"
    alias.symlink_to(first, target_is_directory=True)
    authorized = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=alias,
        user_confirmed=True,
        home=tmp_path / "other-home",
    ).authorize_file(Path("history.jsonl"))

    with authorized.open_binary() as handle:
        assert handle.read() == b"safe\n"
    alias.unlink()
    alias.symlink_to(second, target_is_directory=True)

    with pytest.raises(SourceAccessError) as captured:
        authorized.open_binary()

    assert captured.value.code is SourceAccessErrorCode.SOURCE_IDENTITY_CHANGED


def test_live_adapter_keeps_symlink_refusal_in_existing_issue_taxonomy() -> None:
    assert (
        _scan_failure_code(SourceAccessErrorCode.SYMLINK_NOT_ALLOWED)
        is ScanIssueCode.UNSUPPORTED_SCHEMA
    )


def test_live_adapter_reports_an_oversized_file_as_file_too_large() -> None:
    """The open path and the discovery path must agree on the size reason."""
    assert _scan_failure_code(SourceAccessErrorCode.FILE_TOO_LARGE) is ScanIssueCode.FILE_TOO_LARGE


def test_live_adapter_runner_persists_symlink_refusal_as_unsupported_schema(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "source"
    nested = root / "nested"
    nested.mkdir(parents=True)
    source = nested / "rollout-test.jsonl"
    source.write_text("{}\n", encoding="utf-8")
    moved = tmp_path / "outside"
    grant = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    )
    descriptor = SourceDescriptor(
        source_id="fixture",
        display_name="Fixture",
        storage_kinds=frozenset({StorageKind.JSONL}),
        capabilities=SourceCapabilities(
            can_append=True,
            can_observe_edits=True,
            can_observe_deletes=False,
            can_reconcile_complete_set=False,
        ),
    )
    adapter = JsonlSourceAdapter(
        descriptor=descriptor,
        grant=grant,
        parser=lambda *_args: ParsedSkip(ScanIssueCode.NO_TEXT),
    )
    database = tmp_path / "one-memory.sqlite3"
    jobs = ImportJobStore(database)
    job = jobs.create(user_id="local", source_id="fixture")
    real_open = os.open
    swapped = False

    monkeypatch.setattr(access_module, "_supports_descriptor_relative_open", lambda: True)

    def swap_then_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal swapped
        if not swapped and path == "nested" and dir_fd is not None:
            swapped = True
            nested.rename(moved)
            nested.symlink_to(moved, target_is_directory=True)
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(access_module.os, "open", swap_then_open)
    completed = asyncio.run(
        ImportRunner(
            evidence_store=EvidenceStore(database),
            job_store=jobs,
        ).run(adapter=adapter, job_id=job.job_id)
    )

    assert swapped
    assert completed.status is ImportStatus.COMPLETED
    assert completed.discovered == 1
    assert completed.stored == 0
    assert completed.skipped == 0
    assert completed.errors == 1
    issues = jobs.list_issues(job.job_id)
    assert [(issue.kind, issue.reason) for issue in issues] == [("error", "unsupported_schema")]


@pytest.mark.parametrize("failure", [TypeError("unsupported"), NotImplementedError("unsupported")])
@pytest.mark.parametrize("stage", ["root", "intermediate", "final"])
def test_descriptor_open_capability_errors_fail_closed(
    tmp_path, monkeypatch, failure, stage
) -> None:
    root = tmp_path / "source"
    nested = root / "nested"
    nested.mkdir(parents=True)
    source = nested / "history.jsonl"
    source.write_bytes(b"safe\n")
    authorized = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    ).authorize_file(Path("nested/history.jsonl"))
    real_open = os.open
    calls = 0
    failing_call = {"root": 1, "intermediate": 2, "final": 3}[stage]

    monkeypatch.setattr(access_module, "_supports_descriptor_relative_open", lambda: True)

    def fail_at_stage(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal calls
        calls += 1
        if calls == failing_call:
            raise failure
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(access_module.os, "open", fail_at_stage)
    with pytest.raises(SourceAccessError) as captured:
        authorized.open_binary()

    assert captured.value.code is SourceAccessErrorCode.SOURCE_UNAVAILABLE


@pytest.mark.parametrize("failure", [TypeError("unsupported"), NotImplementedError("unsupported")])
def test_canonical_root_open_capability_errors_fail_closed(tmp_path, monkeypatch, failure) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    source = actual / "history.jsonl"
    source.write_bytes(b"safe\n")
    alias = tmp_path / "chosen"
    alias.symlink_to(actual, target_is_directory=True)
    authorized = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=alias,
        user_confirmed=True,
        home=tmp_path / "other-home",
    ).authorize_file(Path("history.jsonl"))
    real_open = os.open
    calls = 0

    monkeypatch.setattr(access_module, "_supports_descriptor_relative_open", lambda: True)

    def fail_canonical_open(path, flags, mode=0o777, *, dir_fd=None):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise failure
        if dir_fd is None:
            return real_open(path, flags, mode)
        return real_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(access_module.os, "open", fail_canonical_open)
    with pytest.raises(SourceAccessError) as captured:
        authorized.open_binary()

    assert captured.value.code is SourceAccessErrorCode.SOURCE_UNAVAILABLE


@pytest.mark.parametrize("failure", [TypeError("unsupported"), NotImplementedError("unsupported")])
@pytest.mark.parametrize("stage", ["root", "intermediate", "final"])
def test_descriptor_fstat_capability_errors_fail_closed(
    tmp_path, monkeypatch, failure, stage
) -> None:
    root = tmp_path / "source"
    nested = root / "nested"
    nested.mkdir(parents=True)
    source = nested / "history.jsonl"
    source.write_bytes(b"safe\n")
    authorized = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    ).authorize_file(Path("nested/history.jsonl"))
    real_fstat = os.fstat
    calls = 0
    failing_call = {"root": 1, "intermediate": 2, "final": 3}[stage]

    monkeypatch.setattr(access_module, "_supports_descriptor_relative_open", lambda: True)

    def fail_at_stage(descriptor):
        nonlocal calls
        calls += 1
        if calls == failing_call:
            raise failure
        return real_fstat(descriptor)

    monkeypatch.setattr(access_module.os, "fstat", fail_at_stage)
    with pytest.raises(SourceAccessError) as captured:
        authorized.open_binary()

    assert captured.value.code is SourceAccessErrorCode.SOURCE_UNAVAILABLE


def test_descriptor_cleanup_attempts_every_close_when_one_close_raises(monkeypatch) -> None:
    attempted = []

    def failing_close(descriptor):
        attempted.append(descriptor)
        if descriptor == 12:
            raise OSError("injected close failure")

    monkeypatch.setattr(access_module.os, "close", failing_close)

    assert not access_module._close_descriptors([10, 11, 12])
    assert attempted == [12, 11, 10]


def test_descriptor_relative_open_fails_closed_before_open_when_unsupported(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "history.jsonl"
    source.write_bytes(b"safe\n")
    authorized = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    ).authorize_file(source)
    opened = []

    monkeypatch.setattr(access_module.os, "supports_dir_fd", set())
    monkeypatch.setattr(access_module.os, "open", lambda *args, **kwargs: opened.append(args))

    with pytest.raises(SourceAccessError) as captured:
        authorized.open_binary()

    assert captured.value.code is SourceAccessErrorCode.SOURCE_UNAVAILABLE
    assert opened == []


def test_descriptor_relative_open_closes_every_acquired_fd_on_final_fstat_failure(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "source"
    nested = root / "nested"
    nested.mkdir(parents=True)
    source = nested / "history.jsonl"
    source.write_bytes(b"safe\n")
    authorized = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    ).authorize_file(Path("nested/history.jsonl"))
    real_open = os.open
    real_fstat = os.fstat
    real_close = os.close
    opened: list[int] = []
    closed: list[int] = []

    monkeypatch.setattr(access_module, "_supports_descriptor_relative_open", lambda: True)

    def recording_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = (
            real_open(path, flags, mode)
            if dir_fd is None
            else real_open(path, flags, mode, dir_fd=dir_fd)
        )
        opened.append(descriptor)
        return descriptor

    def fail_final_fstat(descriptor):
        if len(opened) == 3 and descriptor == opened[-1]:
            raise OSError("injected final fstat failure")
        return real_fstat(descriptor)

    def recording_close(descriptor):
        closed.append(descriptor)
        return real_close(descriptor)

    monkeypatch.setattr(access_module.os, "open", recording_open)
    monkeypatch.setattr(access_module.os, "fstat", fail_final_fstat)
    monkeypatch.setattr(access_module.os, "close", recording_close)

    with pytest.raises(SourceAccessError) as captured:
        authorized.open_binary()

    assert captured.value.code is SourceAccessErrorCode.SOURCE_UNAVAILABLE
    assert sorted(closed) == sorted(opened)
    assert len(closed) == len(set(closed))


def test_descriptor_relative_open_closes_final_fd_when_fdopen_fails(tmp_path, monkeypatch) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "history.jsonl"
    source.write_bytes(b"safe\n")
    authorized = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    ).authorize_file(source)
    real_open = os.open
    real_close = os.close
    opened: list[int] = []
    closed: list[int] = []

    monkeypatch.setattr(access_module, "_supports_descriptor_relative_open", lambda: True)

    def recording_open(path, flags, mode=0o777, *, dir_fd=None):
        descriptor = (
            real_open(path, flags, mode)
            if dir_fd is None
            else real_open(path, flags, mode, dir_fd=dir_fd)
        )
        opened.append(descriptor)
        return descriptor

    def recording_close(descriptor):
        closed.append(descriptor)
        return real_close(descriptor)

    monkeypatch.setattr(access_module.os, "open", recording_open)
    monkeypatch.setattr(access_module.os, "close", recording_close)
    monkeypatch.setattr(
        access_module.os,
        "fdopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("injected fdopen failure")),
    )

    with pytest.raises(SourceAccessError) as captured:
        authorized.open_binary()

    assert captured.value.code is SourceAccessErrorCode.SOURCE_UNAVAILABLE
    assert sorted(closed) == sorted(opened)
    assert len(closed) == len(set(closed))


def test_stream_close_failure_cannot_replace_directory_cleanup_refusal(
    tmp_path, monkeypatch
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "history.jsonl"
    source.write_bytes(b"safe\n")
    authorized = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    ).authorize_file(source)
    real_fdopen = os.fdopen
    real_close_descriptors = access_module._close_descriptors

    class RaisingClose:
        def __init__(self, descriptor):
            self._stream = real_fdopen(descriptor, "rb")

        def close(self):
            self._stream.close()
            raise OSError("injected stream close failure")

    def close_directories_then_report_failure(descriptors):
        assert real_close_descriptors(descriptors)
        return False

    monkeypatch.setattr(
        access_module,
        "_close_descriptors",
        close_directories_then_report_failure,
    )
    monkeypatch.setattr(
        access_module.os,
        "fdopen",
        lambda descriptor, _mode: RaisingClose(descriptor),
    )

    with pytest.raises(SourceAccessError) as captured:
        authorized.open_binary()

    assert captured.value.code is SourceAccessErrorCode.SOURCE_UNAVAILABLE


def test_authorized_file_is_size_bounded_and_opened_as_a_regular_file(tmp_path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    session = root / "session.jsonl"
    session.write_bytes(b"12345")
    grant = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
        max_file_bytes=4,
    )

    with pytest.raises(SourceAccessError) as captured:
        grant.authorize_file(session)

    assert captured.value.code is SourceAccessErrorCode.FILE_TOO_LARGE


def test_root_symlink_is_resolved_once_but_child_symlinks_remain_forbidden(
    tmp_path,
) -> None:
    actual = tmp_path / "actual"
    actual.mkdir()
    session = actual / "session.jsonl"
    session.write_bytes(b"safe")
    root_link = tmp_path / "source-link"
    root_link.symlink_to(actual, target_is_directory=True)

    grant = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root_link,
        user_confirmed=True,
        home=tmp_path / "other-home",
    )
    authorized = grant.authorize_file(Path("session.jsonl"))

    with authorized.open_binary() as source:
        assert source.read() == b"safe"
    assert authorized.root == actual


def test_external_labels_do_not_expose_absolute_paths() -> None:
    workspace = opaque_workspace_id("cursor", "/Users/private/company/project")
    conversation = opaque_conversation_error_id("cursor", "private-session-id")

    assert workspace and "/Users" not in workspace
    assert conversation and "private-session-id" not in conversation
    assert safe_project_label("/Users/private/company/project") == "project"


def test_sqlite_uri_percent_escapes_cannot_leave_authorized_root(tmp_path) -> None:
    root = tmp_path / "allowed" / "nested"
    root.mkdir(parents=True)
    outside = tmp_path / "secret.db"
    connection = sqlite3.connect(outside)
    connection.execute("CREATE TABLE secret (value TEXT)")
    connection.execute("INSERT INTO secret VALUES ('must-not-read')")
    connection.commit()
    connection.close()
    decoy = root / "%2e%2e%2f%2e%2e%2fsecret.db"
    decoy.write_text("not a database", encoding="utf-8")
    grant = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path,
    )
    source_file = grant.authorize_file(decoy)

    with (
        open_sqlite_readonly(source_file) as authorized,
        pytest.raises(sqlite3.DatabaseError),
    ):
        authorized.execute("SELECT value FROM secret").fetchone()


def test_authorized_file_rejects_inode_replacement_after_authorization(
    tmp_path,
) -> None:
    root = tmp_path / "source"
    root.mkdir()
    source = root / "history.jsonl"
    source.write_text('{"safe":true}\n', encoding="utf-8")
    grant = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    )
    authorized = grant.authorize_file(source)
    # Hold the original inode open so the filesystem cannot hand the same
    # number straight back. Without this the test passes or fails on ext4's
    # free-list mood rather than on the guard, which is how it spent a while
    # looking like a flaky failure. The reuse case is its own test below.
    held = os.open(source, os.O_RDONLY)
    try:
        source.unlink()
        source.write_text('{"replacement":true}\n', encoding="utf-8")
        assert os.stat(source).st_ino != os.fstat(held).st_ino

        with pytest.raises(SourceAccessError) as captured:
            authorized.open_binary()
    finally:
        os.close(held)

    assert captured.value.code is SourceAccessErrorCode.SOURCE_UNAVAILABLE


def test_a_recycled_inode_cannot_slip_past_the_replacement_check(
    tmp_path,
) -> None:
    """A replacement that reuses the inode number is still a different file.

    ext4 reuses an inode number as soon as it is free, so an unlink followed
    by a rewrite routinely lands on the same number and the check sees the
    authorized file. The opening check also pins the stable creation time when
    the platform exposes one, without comparing size or mtime: an ordinary
    append changes those and must remain readable.
    """
    root = tmp_path / "source"
    root.mkdir()
    source = root / "history.jsonl"
    source.write_text('{"safe":true}\n', encoding="utf-8")
    grant = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    )
    authorized = grant.authorize_file(source)
    before = os.stat(source)
    source.unlink()
    source.write_text('{"replacement":true}\n', encoding="utf-8")
    after = os.stat(source)
    if _changed_ns(after) == 0:
        # Linux's stat_result has no portable birth time. A same-inode
        # replacement is indistinguishable there with this low-level guard,
        # but the scanner's size/mtime classification still treats it as
        # changed; do not turn an unavailable platform field into a silent pass.
        pytest.skip("platform has no stable creation-time field")
    assert _changed_ns(after) != _changed_ns(before), "birth time must distinguish the replacement"

    # The filesystem may or may not recycle the inode. Model the branch
    # deterministically: the opening identity has the replacement's inode
    # number (as ext4 would emit after reuse) but the original birth time.
    recycled = replace(
        authorized,
        file_identity=f"{after.st_dev}:{after.st_ino}",
        changed_ns=_changed_ns(before),
    )
    with pytest.raises(SourceAccessError) as captured:
        recycled.open_binary()

    assert captured.value.code is SourceAccessErrorCode.SOURCE_UNAVAILABLE


def test_appending_to_an_authorized_file_keeps_the_creation_time_guard(tmp_path) -> None:
    """The replacement guard must not turn a normal append into a refusal."""
    root = tmp_path / "source"
    root.mkdir()
    source = root / "history.jsonl"
    source.write_text('{"safe":true}\n', encoding="utf-8")
    grant = AuthorizedSourceRoot.grant(
        source_id="fixture",
        root=root,
        user_confirmed=True,
        home=tmp_path / "other-home",
    )
    authorized = grant.authorize_file(source)

    if _changed_ns(os.stat(source)) == 0:
        pytest.skip("platform has no stable creation-time field")
    with source.open("a", encoding="utf-8") as stream:
        stream.write('{"appended":true}\n')

    with authorized.open_binary() as handle:
        assert handle.read() == b'{"safe":true}\n{"appended":true}\n'
