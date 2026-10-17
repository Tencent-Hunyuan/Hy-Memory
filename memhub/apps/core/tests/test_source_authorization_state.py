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

    state = store.authorization_state(
        user_id="local", source_id="workbuddy", candidate=root
    )

    assert state is SourceAuthorizationState.OK


def test_a_revoked_grant_is_not_a_missing_one(tmp_path) -> None:
    store, _database, home = build_store(tmp_path)
    root = _grant(store, home, "workbuddy")
    store.revoke(user_id="local", source_id="workbuddy")

    state = store.authorization_state(
        user_id="local", source_id="workbuddy", candidate=root
    )

    assert state is SourceAuthorizationState.REVOKED_GRANT


def test_a_rebuilt_directory_is_not_a_moved_candidate(tmp_path) -> None:
    store, _database, home = build_store(tmp_path)
    root = _grant(store, home, "workbuddy")
    root.rmdir()
    root.mkdir()

    state = store.authorization_state(
        user_id="local", source_id="workbuddy", candidate=root
    )

    assert state is SourceAuthorizationState.SOURCE_IDENTITY_CHANGED


def test_a_missing_directory_is_named_unavailable(tmp_path) -> None:
    store, _database, home = build_store(tmp_path)
    root = _grant(store, home, "workbuddy")
    root.rmdir()

    state = store.authorization_state(
        user_id="local", source_id="workbuddy", candidate=root
    )

    assert state is SourceAuthorizationState.SOURCE_ROOT_UNAVAILABLE


def test_a_grant_pointing_somewhere_else_is_a_moved_candidate(tmp_path) -> None:
    store, _database, home = build_store(tmp_path)
    _grant(store, home, "workbuddy")
    elsewhere = home / "workbuddy-elsewhere"
    elsewhere.mkdir()

    state = store.authorization_state(
        user_id="local", source_id="workbuddy", candidate=elsewhere
    )

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

    assert (
        capture_rejection(database, "workbuddy") is CaptureRejection.SYNC_DISABLED
    )


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
        (
            database.parent
            / "logs"
            / "capture-rejection-workbuddy-sync_disabled.json"
        ).read_text(encoding="utf8")
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
