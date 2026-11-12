import sqlite3
import time
from contextlib import closing

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from one_memory.context_access import resolve_context_authority
from one_memory.context_models import ContextSearchRequest
from one_memory.external_memory_access import ExternalMemoryAccess


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "context.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TABLE source_grants(user_id TEXT, source_id TEXT, revoked_at TEXT)")
        db.execute("INSERT INTO source_grants VALUES('local','cursor',NULL)")
        db.execute("INSERT INTO source_grants VALUES('local','codex',NULL)")
        db.execute("INSERT INTO source_grants VALUES('local','revoked','2026-09-19')")
        db.execute("INSERT INTO source_grants VALUES('other','cursor','2026-09-19')")
    return path


def access(tools, scopes):
    return ExternalMemoryAccess(
        "00000000-0000-0000-0000-000000000001", 1, frozenset(scopes), frozenset(tools)
    )


def resolve(database, grant, **query):
    return resolve_context_authority(
        ContextSearchRequest(query="a question", **query),
        grant,
        database,
        deadline=time.monotonic() + 2,
    )


def test_memory_only_never_reads_raw_authority(database, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("raw authority read"))
    result = resolve(database, access(["om_recall"], ["all-apps", "source:cursor"]))
    assert result.memory_allowed and result.memory_scopes == frozenset({"all-apps"})
    assert not result.raw_allowed and result.raw_sources == frozenset()


def test_all_apps_raw_reads_every_connected_source_and_removes_revoked_sources(database):
    result = resolve(database, access(["om_conversations"], ["all-apps", "source:cursor"]))
    assert result.raw_allowed and result.raw_sources == frozenset({"cursor", "codex"})
    assert not result.memory_allowed


@pytest.mark.parametrize("scopes", [["source:cursor"], []])
def test_raw_requires_all_apps(database, scopes):
    with pytest.raises(HTTPException) as error:
        resolve(database, access(["om_conversations"], scopes))
    assert error.value.status_code == 403


@pytest.mark.parametrize("tools", [[], ["om_ask"], ["om_matters"]])
def test_other_tools_do_not_imply_context_permission(database, tools):
    with pytest.raises(HTTPException) as error:
        resolve(database, access(tools, ["all-apps", "source:cursor"]))
    assert error.value.status_code == 403


def test_source_scope_alone_does_not_grant_memory(database):
    with pytest.raises(HTTPException):
        resolve(database, access(["om_recall"], ["source:cursor"]))


def test_requested_kinds_cannot_substitute_an_authorized_different_path(database):
    with pytest.raises(HTTPException):
        resolve(database, access(["om_recall"], ["all-apps"]), kinds=("raw",))


@pytest.mark.parametrize("source", ["other", "revoked"])
def test_explicit_raw_filter_must_be_currently_authorized(database, source):
    with pytest.raises(HTTPException) as error:
        resolve(
            database,
            access(["om_recall", "om_conversations"], ["all-apps"]),
            raw_source_ids=(source,),
        )
    assert error.value.status_code == 403


def test_explicit_raw_filter_cannot_hide_missing_raw_permission(database):
    with pytest.raises(HTTPException):
        resolve(database, access(["om_recall"], ["all-apps"]), raw_source_ids=("cursor",))


def test_filters_do_not_change_memory_scope(database):
    workspace = "workspace:v1:" + "a" * 64
    result = resolve(
        database,
        access(["om_recall", "om_conversations"], ["all-apps", workspace, "source:cursor"]),
        raw_source_ids=("cursor",),
    )
    assert result.memory_scopes == frozenset({"all-apps", workspace})
    assert result.raw_sources == frozenset({"cursor"})


def test_explicit_raw_filter_accepts_any_connected_source_under_all_apps(database):
    result = resolve(
        database,
        access(["om_conversations"], ["all-apps", "source:cursor"]),
        raw_source_ids=("codex",),
    )
    assert result.raw_allowed and result.raw_sources == frozenset({"codex"})


def test_no_connected_raw_source_can_be_reported_beside_allowed_memory(database):
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE source_grants SET revoked_at='2026-09-20' WHERE user_id='local'")
    result = resolve(database, access(["om_recall", "om_conversations"], ["all-apps"]))
    assert result.memory_allowed and not result.raw_allowed


def test_local_selection_does_not_create_or_initialize_a_database(tmp_path):
    path = tmp_path / "absent.sqlite3"
    result = resolve(path, None)
    assert result.raw_allowed and result.raw_sources is None
    assert result.memory_allowed and result.memory_scopes is None
    assert not path.exists()


def test_expired_deadline_does_not_open_database(database, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("expired read"))
    with pytest.raises(TimeoutError):
        resolve_context_authority(
            ContextSearchRequest(query="x"),
            access(["om_conversations"], ["all-apps"]),
            database,
            deadline=time.monotonic() - 1,
        )


@pytest.mark.parametrize(
    "invalid",
    [
        {"query": " "},
        {"kinds": []},
        {"kinds": ["raw", "raw"]},
        {"kinds": ["ask"]},
        {"kinds": ["memory"], "raw_source_ids": ["cursor"]},
        {"raw_source_ids": []},
        {"raw_source_ids": ["cursor", "cursor"]},
        {"raw_source_ids": ["../secret"]},
        {"limit": True},
        {"limit": 21},
        {"limit": 0},
        {"timeout_ms": 49},
        {"character_budget": 199},
        {"user_id": "other"},
        {"scope": "*"},
        {"workspace_path": "/private"},
        {"source_ids": ["cursor"]},
    ],
)
def test_request_rejects_ambiguous_or_authority_fields(invalid):
    with pytest.raises(ValidationError):
        ContextSearchRequest.model_validate({"query": "question", **invalid})
