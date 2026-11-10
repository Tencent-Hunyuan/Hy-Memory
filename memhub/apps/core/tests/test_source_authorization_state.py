"""Why a source cannot be read, named separately from whether it can.

`authorized=false` was one answer to six different situations, so the UI could
only offer every recovery at once and people reinstalled plugins for a problem
in the source grant (issue #29). These cover the naming, and the capture-side
refusal that used to be silent and unrecorded.
"""

import json
from pathlib import Path

import pytest

from one_memory.imports.access import SourceAccessError, SourceAccessErrorCode
from one_memory.imports.grants import SourceAuthorizationState, SourceGrantStore
from one_memory.imports.sources.registry import RootCandidateKind
from one_memory.live_capture import (
    CaptureRejection,
    capture_rejection,
    record_capture_rejection,
)
from one_memory.memory_server.config_state import ConfigStore


def build_store(tmp_path: Path) -> tuple[SourceGrantStore, Path, Path]:
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    database = tmp_path / "one-memory.sqlite3"
    return SourceGrantStore(database, home=home), database, home


def _grant(store: SourceGrantStore, home: Path, name: str) -> Path:
    root = home / name
    root.mkdir(parents=True, exist_ok=True)
    store.grant(
        user_id="local",
        source_id="workbuddy",
        root=root,
        candidate_kind=RootCandidateKind.DEFAULT,
    )
    return root


def test_no_grant_is_named_missing(tmp_path) -> None:
    store, _database, home = build_store(tmp_path)

    state = store.authorization_state(
        user_id="local", source_id="workbuddy", candidate=home / "workbuddy"
    )

    assert state is SourceAuthorizationState.MISSING_GRANT


def test_a_live_grant_on_the_current_candidate_is_ok(tmp_path) -> None:
    store, _database, home = build_store(tmp_path)
    root = _grant(store, home, "workbuddy")

    state = store.authorization_state(user_id="local", source_id="workbuddy", candidate=root)

    assert state is SourceAuthorizationState.OK


def test_a_revoked_grant_is_not_a_missing_one(tmp_path) -> None:
    store, _database, home = build_store(tmp_path)
    root = _grant(store, home, "workbuddy")
    store.revoke(user_id="local", source_id="workbuddy")

    state = store.authorization_state(user_id="local", source_id="workbuddy", candidate=root)

    assert state is SourceAuthorizationState.REVOKED_GRANT


def test_a_rebuilt_directory_is_not_a_moved_candidate(tmp_path) -> None:
    store, _database, home = build_store(tmp_path)
    root = _grant(store, home, "workbuddy")
    root.rmdir()
    root.mkdir()

    state = store.authorization_state(user_id="local", source_id="workbuddy", candidate=root)

    assert state is SourceAuthorizationState.SOURCE_IDENTITY_CHANGED


def test_a_missing_directory_is_named_unavailable(tmp_path) -> None:
    store, _database, home = build_store(tmp_path)
    root = _grant(store, home, "workbuddy")
    root.rmdir()

    state = store.authorization_state(user_id="local", source_id="workbuddy", candidate=root)

    assert state is SourceAuthorizationState.SOURCE_ROOT_UNAVAILABLE


def test_a_grant_pointing_somewhere_else_is_a_moved_candidate(tmp_path) -> None:
    store, _database, home = build_store(tmp_path)
    _grant(store, home, "workbuddy")
    elsewhere = home / "workbuddy-elsewhere"
    elsewhere.mkdir()

    state = store.authorization_state(user_id="local", source_id="workbuddy", candidate=elsewhere)

    assert state is SourceAuthorizationState.CANDIDATE_CHANGED


def test_a_grant_still_fails_closed_when_a_state_is_named(tmp_path) -> None:
    store, _database, home = build_store(tmp_path)
    root = _grant(store, home, "workbuddy")
    root.rmdir()
    root.mkdir()

    # Naming the reason is not granting access: the read path still refuses,
    # and says so through the same code it always did.
    with pytest.raises(SourceAccessError) as captured:
        store.authorized_root(user_id="local", source_id="workbuddy")
    assert captured.value.code is SourceAccessErrorCode.SOURCE_IDENTITY_CHANGED


def test_capture_rejection_names_sync_disabled(tmp_path) -> None:
    store, database, home = build_store(tmp_path)
    _grant(store, home, "workbuddy")

    assert capture_rejection(database, "workbuddy") is CaptureRejection.SYNC_DISABLED


def test_capture_rejection_names_a_missing_then_a_revoked_grant(tmp_path) -> None:
    _store, database, _home = build_store(tmp_path)
    ConfigStore(database.parent).set_sync("workbuddy", True)

    assert capture_rejection(database, "workbuddy") is CaptureRejection.GRANT_MISSING

    store, _database, home = build_store(tmp_path)
    _grant(store, home, "workbuddy")
    store.revoke(user_id="local", source_id="workbuddy")

    assert capture_rejection(database, "workbuddy") is CaptureRejection.GRANT_REVOKED


def test_capture_rejection_is_none_when_a_turn_may_be_captured(tmp_path) -> None:
    store, database, home = build_store(tmp_path)
    _grant(store, home, "workbuddy")
    ConfigStore(database.parent).set_sync("workbuddy", True)

    assert capture_rejection(database, "workbuddy") is None


def test_the_recorded_rejection_carries_no_turn_and_no_path(tmp_path) -> None:
    store, database, home = build_store(tmp_path)
    _grant(store, home, "workbuddy")
    secret = "深绿色的按钮，不要告诉别人"
    marker = home / "secret.txt"
    marker.write_text(secret, encoding="utf8")

    record_capture_rejection(database, "workbuddy", CaptureRejection.SYNC_DISABLED)
    record_capture_rejection(database, "workbuddy", CaptureRejection.SYNC_DISABLED)

    record = json.loads(
        (database.parent / "logs" / "capture-rejection-workbuddy-sync_disabled.json").read_text(
            encoding="utf8"
        )
    )
    assert record["count"] == 2
    assert record["source_id"] == "workbuddy"
    assert record["reason"] == "sync_disabled"
    assert secret not in json.dumps(record, ensure_ascii=False)
    assert str(home) not in json.dumps(record, ensure_ascii=False)


def test_recording_a_rejection_never_breaks_the_host_turn(tmp_path) -> None:
    store, database, home = build_store(tmp_path)
    _grant(store, home, "workbuddy")
    # A data directory that cannot be written must stay silent: this runs
    # inside somebody's turn, where raising would print into the conversation.
    blocked = tmp_path / "blocked"
    blocked.write_text("not a directory", encoding="utf8")

    record_capture_rejection(
        blocked / "one-memory.sqlite3",
        "workbuddy",
        CaptureRejection.GRANT_REVOKED,
    )


def test_a_truncated_marker_is_repaired_instead_of_disabling_the_sink(tmp_path) -> None:
    """A half-written marker must not stay broken forever.

    The record is read by support as-is, and ``record_capture_rejection``
    swallows every error by design.  If a truncated marker made ``json.loads``
    raise on every later call, the count would silently stop being maintained.
    """
    store, database, home = build_store(tmp_path)
    _grant(store, home, "workbuddy")

    logs = database.parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "capture-rejection-workbuddy-sync_disabled.json").write_text(
        '{"count": 4', encoding="utf8"
    )

    record_capture_rejection(database, "workbuddy", CaptureRejection.SYNC_DISABLED)

    record = json.loads(
        (logs / "capture-rejection-workbuddy-sync_disabled.json").read_text(encoding="utf8")
    )
    assert record["count"] == 1
    assert record["source_id"] == "workbuddy"


def test_the_marker_tracks_when_the_last_rejection_happened(tmp_path, monkeypatch) -> None:
    """``last_at`` must move with every rejection, not stay at the window start."""
    import one_memory.live_capture as live_capture

    store, database, home = build_store(tmp_path)
    _grant(store, home, "workbuddy")
    marker = database.parent / "logs" / "capture-rejection-workbuddy-sync_disabled.json"
    clock = iter([1_000.0, 1_060.0])
    real_time = live_capture.time.time
    monkeypatch.setattr(live_capture.time, "time", lambda: next(clock, real_time()))

    record_capture_rejection(database, "workbuddy", CaptureRejection.SYNC_DISABLED)
    first = json.loads(marker.read_text(encoding="utf8"))
    record_capture_rejection(
        database, "workbuddy", CaptureRejection.SYNC_DISABLED, window_seconds=1e12
    )
    second = json.loads(marker.read_text(encoding="utf8"))

    assert first["last_at"] == first["window_started_at"]
    assert second["count"] == 2
    assert second["window_started_at"] == first["window_started_at"]
    assert second["last_at"] > second["window_started_at"]


def test_a_failed_publish_leaves_the_previous_marker_intact(tmp_path, monkeypatch) -> None:
    """A write that fails mid-way keeps the old record and leaves no stray file."""
    store, database, home = build_store(tmp_path)
    _grant(store, home, "workbuddy")

    record_capture_rejection(database, "workbuddy", CaptureRejection.SYNC_DISABLED)
    marker = database.parent / "logs" / "capture-rejection-workbuddy-sync_disabled.json"
    before = marker.read_text(encoding="utf8")

    def boom(_self, _target):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "replace", boom)
    record_capture_rejection(database, "workbuddy", CaptureRejection.SYNC_DISABLED)

    assert marker.read_text(encoding="utf8") == before
    assert [p.name for p in marker.parent.iterdir()] == [marker.name]
