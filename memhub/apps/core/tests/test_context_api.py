import asyncio
import hashlib
import json
import sqlite3
import time
from contextlib import closing
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.database_access import close_main_databases, main_database
from one_memory.imports.storage import SEARCH_SCHEMA
from tests.test_memory_library import database as database  # noqa: F401
from tests.test_memory_library import grant, put

URL = "/api/v1/context/search"
AUTH = {"Authorization": "Bearer context-test-token"}


@pytest.fixture
def app(database, tmp_path):
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("UPDATE evidence SET verbatim='needle original statement'")
        db.executescript(SEARCH_SCHEMA)
        db.execute("INSERT INTO evidence_search(evidence_search) VALUES('rebuild')")
        put(db, "memory", content="needle governed summary")
    application = create_app(
        Settings(
            data_dir=tmp_path / "unused",
            source_home=tmp_path,
            api_token=SecretStr("context-test-token"),
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )
    application.state.evidence_store = SimpleNamespace(database_path=database)
    return application


def headers(tools, scopes=("all-apps", "source:cursor")):
    return {
        **AUTH,
        "x-one-memory-read-context": "external",
        "x-one-memory-installation-id": "00000000-0000-0000-0000-000000000001",
        "x-one-memory-installation-revision": "1",
        "x-one-memory-allowed-scopes": json.dumps(scopes),
        "x-one-memory-allowed-tools": json.dumps(tools),
    }


def post(app, body=None, auth=None):
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.post(
                URL, json=body or {"query": "needle"}, headers=AUTH if auth is None else auth
            )

    return asyncio.run(run())


def test_no_model_local_query_returns_real_raw_and_governed_items(app):
    response = post(app)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["state"] == "grounded"
    assert {item["kind"] for item in body["items"]} == {"raw", "memory"}
    assert body["schema_version"] == 1 and body["adoption"] == "unknown"
    assert body["used_characters"] == sum(
        len(item["content"])
        if item["kind"] == "memory"
        else sum(len(message["text"]) for message in item["messages"])
        for item in body["items"]
    )
    assert all(item["revision"] == 7 for item in body["items"] if item["kind"] == "memory")


@pytest.mark.parametrize(
    "tool,kind,other", [("om_recall", "memory", "raw"), ("om_conversations", "raw", "memory")]
)
def test_existing_single_lane_authorization_never_runs_denied_reader(
    app, monkeypatch, tool, kind, other
):
    from one_memory import context_routes

    target = "read_raw_indexed_context" if other == "raw" else "read_memory_context"
    monkeypatch.setattr(
        context_routes,
        target,
        lambda *a, **k: pytest.fail("denied reader was invoked"),
    )
    response = post(app, auth=headers([tool]))
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "partial"
    assert {item["kind"] for item in body["items"]} == {kind}
    assert body["paths"][other]["status"] == "not_authorized"


@pytest.mark.parametrize(
    "auth,code",
    [
        ({}, 401),
        ({"Authorization": "Bearer wrong"}, 401),
        ({**AUTH, "Origin": "https://untrusted.invalid"}, 403),
        (headers(["om_ask"]), 403),
    ],
)
def test_authentication_and_origin_remain_required(app, auth, code):
    assert post(app, auth=auth).status_code == code


@pytest.mark.parametrize(
    "body",
    [{"query": "needle", "raw_source_ids": ["workbuddy"]}, {"query": "needle", "kinds": ["raw"]}],
)
def test_explicit_denial_is_not_masked_by_other_lane(app, body):
    assert post(app, body, headers(["om_recall"])).status_code == 403


def test_source_filter_only_narrows_raw_and_does_not_leak_other_source(app):
    response = post(
        app,
        {"query": "needle", "raw_source_ids": ["cursor"]},
        headers(["om_recall", "om_conversations"]),
    )
    assert response.status_code == 200
    assert response.json()["state"] == "grounded"
    assert all(
        item["source_id"] == "cursor" for item in response.json()["items"] if item["kind"] == "raw"
    )


def test_revocation_during_other_lane_is_rechecked_before_response(app, database, monkeypatch):
    from one_memory import context_routes

    original = context_routes.read_memory_context

    def revoke_after_read(*args, **kwargs):
        found = original(*args, **kwargs)
        with closing(sqlite3.connect(database)) as db, db:
            grant(db, "cursor")
        return found

    monkeypatch.setattr(context_routes, "read_memory_context", revoke_after_read)
    response = post(app, auth=headers(["om_recall", "om_conversations"]))
    assert response.status_code == 200
    assert response.json()["items"] == []
    assert response.json()["state"] == "partial"


def test_generic_context_tracks_active_local_sources_without_per_source_scopes(app, database):
    generic = headers(
        ["om_recall", "om_conversations"], scopes=("all-apps", "raw-imported-all")
    ) | {"x-one-memory-host-id": "generic-local-mcp"}
    with closing(sqlite3.connect(database)) as db, db:
        grant(db, "cursor", revoked=None)
        grant(db, "workbuddy", user="other", revoked=None)
    response = post(app, auth=generic)
    assert response.status_code == 200, response.text
    assert {item["source_id"] for item in response.json()["items"] if item["kind"] == "raw"} == {
        "cursor"
    }
    assert response.json()["paths"]["memory"]["status"] != "not_authorized"
    for source in ("workbuddy", "missing"):
        denied = post(app, {"query": "needle", "raw_source_ids": [source]}, generic)
        assert denied.status_code == 403

    with closing(sqlite3.connect(database)) as db, db:
        grant(db, "workbuddy", revoked=None)
    response = post(app, auth=generic)
    assert {item["source_id"] for item in response.json()["items"] if item["kind"] == "raw"} == {
        "cursor",
        "workbuddy",
    }
    with closing(sqlite3.connect(database)) as db, db:
        grant(db, "cursor", revoked="2026-09-20")
    response = post(app, auth=generic)
    assert {item["source_id"] for item in response.json()["items"] if item["kind"] == "raw"} == {
        "workbuddy"
    }
    with closing(sqlite3.connect(database)) as db, db:
        db.execute("DELETE FROM source_grants WHERE user_id='local' AND source_id='workbuddy'")
    response = post(app, auth=generic)
    assert response.status_code == 200
    assert response.json()["paths"]["raw"]["status"] == "not_authorized"
    assert response.json()["paths"]["memory"]["status"] != "not_authorized"


def test_generic_raw_grant_removed_after_read_is_dropped(app, database, monkeypatch):
    from one_memory import context_routes

    with closing(sqlite3.connect(database)) as db, db:
        grant(db, "cursor", revoked=None)
    generic = headers(
        ["om_recall", "om_conversations"], scopes=("all-apps", "raw-imported-all")
    ) | {"x-one-memory-host-id": "generic-local-mcp"}
    original = context_routes.read_raw_indexed_context

    def remove_grant_after_read(*args, **kwargs):
        found = original(*args, **kwargs)
        assert any(item.source_id == "cursor" for item in found.items)
        with closing(sqlite3.connect(database)) as db, db:
            db.execute("DELETE FROM source_grants WHERE user_id='local' AND source_id='cursor'")
        return found

    monkeypatch.setattr(context_routes, "read_raw_indexed_context", remove_grant_after_read)
    response = post(app, {"query": "needle", "kinds": ["raw"]}, generic)
    assert response.status_code == 200, response.text
    assert response.json()["items"] == []
    assert response.json()["paths"]["raw"]["status"] == "degraded"
    assert response.json()["paths"]["raw"]["reason"] == "evidence_changed"


def test_missing_storage_is_unavailable_not_empty(app, tmp_path):
    app.state.evidence_store.database_path = tmp_path / "missing.sqlite3"
    response = post(app)
    assert response.status_code == 200
    assert response.json()["state"] == "unavailable"
    assert response.json()["items"] == []
    assert not app.state.evidence_store.database_path.exists()


def test_openapi_has_discriminated_items_and_no_caller_authority_fields(app):
    spec = app.openapi()
    operation = spec["paths"][URL]["post"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ContextSearchResponse"
    }
    request = spec["components"]["schemas"]["ContextSearchRequest"]
    assert request["additionalProperties"] is False
    assert set(request["properties"]) == {
        "query",
        "kinds",
        "raw_source_ids",
        "limit",
        "character_budget",
        "timeout_ms",
    }
    response = spec["components"]["schemas"]["ContextSearchResponse"]
    assert response["properties"]["items"]["items"]["discriminator"]["propertyName"] == "kind"
    for payload in ({"user_id": "other"}, {"scope": "*"}, {"source_ids": ["cursor"]}):
        assert post(app, {"query": "needle", **payload}).status_code == 422


def test_request_uses_only_readonly_connections_and_preserves_nonempty_database(
    app, database, monkeypatch
):
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    assert database.stat().st_size > 0
    original = sqlite3.connect
    connections = []

    def observe(path, *args, **kwargs):
        assert "mode=ro" in str(path) and kwargs.get("uri") is True
        connections.append(str(path))
        return original(path, *args, **kwargs)

    close_main_databases()
    reads = main_database(database).stats().reader_checkouts
    monkeypatch.setattr(sqlite3, "connect", observe)
    response = post(app, auth=headers(["om_recall", "om_conversations"]))
    assert response.status_code == 200 and response.json()["items"]
    assert connections
    assert main_database(database).stats().reader_checkouts - reads >= 3
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before


def test_total_result_and_body_budgets_are_shared_across_paths(app):
    response = post(app, {"query": "needle", "limit": 1, "character_budget": 200})
    body = response.json()
    assert response.status_code == 200
    assert len(body["items"]) == 1 and body["used_characters"] <= 200
    assert body["state"] == "grounded" and body["truncated"]
    assert body["paths"]["raw"]["status"] == "ok"
    assert body["paths"]["memory"]["status"] == "ok"
    assert "count_budget" in body["paths"]["memory"]["truncated_by"]


def test_successful_no_matches_is_empty_not_unavailable(app):
    response = post(app, {"query": "no-matching-zzzzzz"})
    assert response.status_code == 200
    assert response.json()["state"] == "empty"
    assert response.json()["paths"]["raw"]["status"] == "empty"
    assert response.json()["paths"]["memory"]["status"] == "empty"


def test_partial_raw_index_cannot_report_complete_empty(app):
    class PartialIndex:
        def lookup_raw(self, query, *, source_ids, deadline):
            from one_memory.retrieval.raw_candidates import RawCandidateBatch

            return RawCandidateBatch(complete=True, reason="no_eligible_chunks"), True

    app.state.retrieval_runtime = PartialIndex()
    response = post(app, {"query": "no-matching-zzzzzz"})
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "empty"
    assert body["paths"]["raw"]["status"] == "empty"
    assert body["items"] == []


def test_governed_semantic_ids_enter_unified_memory_path(app):
    from types import SimpleNamespace

    async def semantic_search(_query: str) -> tuple[str, ...]:
        return ("memory",)

    app.state.recall_target = SimpleNamespace(semantic_search=semantic_search)
    response = post(app, {"query": "职业是什么呀呀呀", "kinds": ["memory"]})
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "grounded"
    memories = [item for item in body["items"] if item["kind"] == "memory"]
    assert [item["memory_id"] for item in memories] == ["memory"]
    assert "governed_semantic" in memories[0]["matched_by"]
    assert "semantic" not in memories[0]["matched_by"]


def test_slow_semantic_search_returns_local_memory_inside_the_deadline(app):
    async def semantic_search(_query: str) -> tuple[str, ...]:
        await asyncio.sleep(10)
        return ()

    app.state.recall_target = SimpleNamespace(semantic_search=semantic_search)
    started = time.monotonic()
    response = post(app, {"query": "needle", "kinds": ["memory"], "timeout_ms": 300})
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    body = response.json()
    assert elapsed < 0.6
    assert [item["memory_id"] for item in body["items"]] == ["memory"]
    assert body["state"] == "partial"
    assert body["paths"]["memory"]["status"] == "degraded"
    assert body["paths"]["memory"]["reason"] == "lexical_only"
