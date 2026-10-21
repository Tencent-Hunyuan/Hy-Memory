import asyncio
import hashlib
import inspect
import sqlite3
import threading
from contextlib import closing
from types import SimpleNamespace

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory import api
from one_memory.config import Settings
from one_memory.hy_memory_bridge import (
    HySdkMemoryPage,
    HySdkMemoryRecord,
    HySdkRawRecord,
)
from one_memory.memory_governance import MemoryGovernanceStore
from one_memory.memory_library import MemoryLibraryPage, read_memory_library
from one_memory.source_read_access import SourceReadAccess
from tests.test_memory_library import NOW, grant, put
from tests.test_memory_library import database as database  # noqa: F401

URL = "/api/v1/memory-library"
AUTH = {"Authorization": "Bearer library-test-token"}


@pytest.fixture
def app(database, tmp_path):
    # Route-only tests: real production schema and SQL, no Runtime/background startup.
    application = api.create_app(
        Settings(
            data_dir=tmp_path / "unused-runtime",
            source_home=tmp_path / "unused-home",
            api_token=SecretStr("library-test-token"),
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )
    application.state.evidence_store = SimpleNamespace(database_path=database)
    return application


def request(app, *, params=None, headers=None, method="GET", url=URL):
    async def run():
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(
                method, url, params=params, headers=AUTH if headers is None else headers
            )

    return asyncio.run(run())


class _SdkLibrary:
    def __init__(self):
        self.memory = HySdkMemoryRecord(
            memory_id="memory-1",
            content="body",
            layer="l2_fact",
            status="active",
            is_latest=True,
            user_id="local",
            agent_id="one-memory",
        )
        self.deleted = []

    async def list_sdk_memories(self, *, limit, offset, **_kwargs):
        return HySdkMemoryPage(
            memories=(self.memory,),
            total=1,
            limit=limit,
            offset=offset,
        )

    async def get_sdk_memory(self, memory_id):
        return self.memory if memory_id == self.memory.memory_id else None

    async def update(self, memory_id, content):
        assert memory_id == self.memory.memory_id
        self.memory = self.memory.model_copy(update={"content": content})
        return {"success": True, "memory_id": memory_id}

    async def delete(self, memory_id):
        self.deleted.append(memory_id)
        return {"success": True, "deleted_count": 1}

    async def list_sdk_raw(self, **_kwargs):
        return (
            (
                HySdkRawRecord(
                    raw_id="raw-1",
                    content="verbatim",
                    user_id="local",
                    agent_id="one-memory",
                    session_id="session",
                    source="cursor",
                ),
            ),
            1,
        )


@pytest.mark.parametrize(
    ("headers", "code"),
    [
        ({}, 401),
        ({"Authorization": "Bearer wrong-token"}, 401),
        ({"Authorization": "Basic library-test-token"}, 401),
        ({**AUTH, "Origin": "https://untrusted.invalid"}, 403),
        (
            {
                **AUTH,
                "x-one-memory-read-context": "external",
                "x-one-memory-installation-id": "00000000-0000-0000-0000-000000000001",
                "x-one-memory-installation-revision": "1",
                "x-one-memory-allowed-scopes": '["all-apps","source:cursor"]',
                "x-one-memory-allowed-tools": '["om_recall"]',
            },
            403,
        ),
    ],
)
def test_auth_and_external_policy_reject_before_reading(app, monkeypatch, headers, code):
    def forbidden(*args, **kwargs):
        pytest.fail("Unauthorized request reached projection")

    monkeypatch.setattr(api, "read_memory_library", forbidden, raising=False)
    response = request(app, headers=headers)
    assert response.status_code == code
    if code == 401:
        assert response.headers["www-authenticate"] == "Bearer"


def test_sdk_native_library_detail_update_delete_and_raw_routes(app):
    sdk = _SdkLibrary()
    app.state.sdk_library_bridge = sdk

    page = request(
        app,
        url="/api/v2/memory-library",
        params=[("statuses", "active"), ("statuses", "superseded")],
    )
    assert page.status_code == 200
    assert page.json()["memories"][0]["memory_id"] == "memory-1"

    detail = request(app, url="/api/v2/memories/memory-1")
    assert detail.status_code == 200
    assert detail.json()["content"] == "body"

    async def mutate():
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            put = await client.put(
                "/api/v2/memories/memory-1",
                headers=AUTH,
                json={"content": "changed"},
            )
            raw = await client.get("/api/v2/raw", headers=AUTH)
            deleted = await client.delete("/api/v2/memories/memory-1", headers=AUTH)
            return put, raw, deleted

    put, raw, deleted = asyncio.run(mutate())
    assert put.status_code == 200
    assert put.json()["content"] == "changed"
    assert raw.status_code == 200
    assert raw.json()["items"][0]["raw_id"] == "raw-1"
    assert deleted.status_code == 204
    assert sdk.deleted == ["memory-1"]


@pytest.mark.parametrize(
    ("parameter", "value"),
    [
        ("status", "unknown"),
        ("status", ""),
        ("active_only", "unknown"),
        ("offset", "-1"),
        ("offset", "1.5"),
        ("offset", "true"),
        ("offset", str(2**63)),
        ("limit", "0"),
        ("limit", "501"),
        ("limit", "1.5"),
        ("limit", "false"),
        ("source", ""),
        ("query", "x" * 513),
    ],
)
def test_invalid_parameters_are_422_before_projection(app, monkeypatch, parameter, value):
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid query reached projection")

    monkeypatch.setattr(api, "read_memory_library", forbidden, raising=False)
    response = request(app, params={parameter: value})
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "request_validation_failed"
    assert any(error["location"] == ["query", parameter] for error in body["errors"])
    assert all("input" not in error for error in body["errors"])


def test_empty_unknown_source_and_query_boundary_are_success_not_failure(app):
    response = request(app)
    assert response.status_code == 200
    assert response.json() == {
        "items": [],
        "total": 0,
        "offset": 0,
        "limit": 100,
        "stale": False,
        "source_ids_by_memory": {},
        "source_time_by_memory": {},
    }
    for params in (
        {"query": ""},
        {"query": "x" * 512},
        {"source": "unknown"},
        {"offset": str(2**63 - 1), "limit": "500"},
    ):
        result = request(app, params=params)
        assert result.status_code == 200
        assert result.json()["total"] == 0


def test_active_only_route_pages_live_memories_and_rejects_status_conflict(app, database):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "a-live")
        put(db, "b-live")
        put(db, "superseded", status="superseded")
    response = request(app, params={"active_only": "true", "limit": 1})
    assert response.status_code == 200
    assert response.json()["total"] == 2
    assert response.json()["items"][0]["memory_id"] == "a-live"
    second = request(app, params={"active_only": "true", "limit": 1, "offset": 1})
    assert second.json()["items"][0]["memory_id"] == "b-live"
    conflict = request(app, params={"active_only": "true", "status": "confirmed"})
    assert conflict.status_code == 422
    assert conflict.json()["detail"] == "active_only_conflicts_with_status"


def test_real_sql_full_pagination_filters_and_trusted_local_user(app, database):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(1007):
            put(db, f"m-{index:04d}", content="literal 50%_\\done", links=("e1", "e3"))
        put(db, "foreign", user="other", links=("other-e",), content="foreign secret")
        put(db, "excluded-source", links=("e2",))
        put(db, "deleted", status="deleted", deleted=NOW)
        put(db, "other-live")
    collected = []
    for offset in (0, 500, 1000):
        response = request(
            app,
            params={
                "query": "50%_\\done",
                "status": "confirmed",
                "source": "cursor",
                "offset": offset,
                "limit": 500,
                "user_id": "other",
            },
            headers={**AUTH, "X-User-Id": "other"},
        )
        assert response.status_code == 200
        page = response.json()
        assert (page["total"], page["offset"], page["limit"]) == (1007, offset, 500)
        expected = [f"m-{index:04d}" for index in range(offset, min(offset + 500, 1007))]
        assert [item["memory_id"] for item in page["items"]] == expected
        assert all(item["user_id"] == "local" for item in page["items"])
        assert all(item["evidence_ids"] == ["e1", "e3"] for item in page["items"])
        assert page["source_ids_by_memory"] == {key: ["cursor"] for key in expected}
        collected.extend(expected)
    assert len(set(collected)) == 1007
    assert request(app, params={"query": "foreign secret", "user_id": "other"}).json()["total"] == 0
    deleted = request(app, params={"status": "deleted"}).json()
    assert [item["memory_id"] for item in deleted["items"]] == ["deleted"]
    assert request(app).json()["total"] == 1009
    beyond = request(app, params={"offset": 2000}).json()
    assert beyond["items"] == [] and beyond["total"] == 1009


def test_revoked_mixed_sources_are_excluded_before_page_and_refresh(app, database):
    with closing(sqlite3.connect(database)) as db, db:
        for index in range(1001):
            put(db, f"blocked-{index:04d}", links=("e1", "e2"))
        put(db, "readable")
        grant(db, "workbuddy", revoked=None)
        grant(db, "cursor", user="other")
    assert request(app).json()["total"] == 1002
    with closing(sqlite3.connect(database)) as db, db:
        grant(db, "workbuddy")
    response = request(app, params={"source": "cursor", "limit": 1})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert [item["memory_id"] for item in response.json()["items"]] == ["readable"]
    assert response.json()["source_ids_by_memory"] == {"readable": ["cursor"]}
    with closing(sqlite3.connect(database)) as db, db:
        db.execute(
            "UPDATE governed_memory_evidence SET active=0 "
            "WHERE memory_id='blocked-0000' AND evidence_id='e2'"
        )
    assert request(app, params={"source": "cursor"}).json()["total"] == 2
    assert request(app, params={"source": "workbuddy"}).json()["total"] == 0


@pytest.mark.parametrize("failure", ["missing", "schema", "json", "model", "date"])
def test_real_storage_failures_return_generic_503_not_empty(app, database, tmp_path, failure):
    if failure == "missing":
        app.state.evidence_store.database_path = tmp_path / "private-missing.sqlite3"
    else:
        with closing(sqlite3.connect(database)) as db, db:
            put(db, "present")
            if failure == "schema":
                db.execute("DROP TABLE source_grants")
            elif failure == "json":
                db.execute("UPDATE governed_memories SET tags_json='private-invalid-json'")
            elif failure == "model":
                db.execute("UPDATE governed_memories SET kind='private-invalid-kind'")
            else:
                db.execute("UPDATE governed_memories SET updated_at='private-invalid-date'")
    response = request(app)
    assert response.status_code == 503
    assert response.json() == {"detail": "memory_library_unavailable"}
    assert not (tmp_path / "private-missing.sqlite3").exists()


@pytest.mark.parametrize("error_type", [sqlite3.DatabaseError, OSError, RuntimeError, TypeError])
def test_projection_exceptions_never_expose_paths_sql_or_content(app, monkeypatch, error_type):
    def fail(*args, **kwargs):
        raise error_type("/private/user/secret.sqlite3 SELECT private-content FROM evidence")

    monkeypatch.setattr(api, "read_memory_library", fail, raising=False)
    response = request(app)
    assert response.status_code == 503
    assert response.json() == {"detail": "memory_library_unavailable"}


def test_route_runs_in_threadpool_passes_typed_filters_and_does_not_write(
    app, database, monkeypatch
):
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "present")
    before = hashlib.sha256(database.read_bytes()).hexdigest()
    event_loop_thread = threading.get_ident()
    calls = []

    def observed(path, **kwargs):
        assert threading.get_ident() != event_loop_thread
        calls.append((path, kwargs))
        return read_memory_library(path, **kwargs)

    def forbidden_init(*args, **kwargs):
        pytest.fail("GET must not construct a schema-writing governance store")

    monkeypatch.setattr(api, "read_memory_library", observed, raising=False)
    monkeypatch.setattr(MemoryGovernanceStore, "__init__", forbidden_init)
    response = request(
        app,
        params={
            "query": "Memory",
            "status": "confirmed",
            "source": "cursor",
            "offset": 0,
            "limit": 1,
        },
    )
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert len(calls) == 1
    path, parameters = calls[0]
    assert path == database
    assert parameters == {
        "active_only": False,
        "user_id": "local",
        "query": "Memory",
        "status": "confirmed",
        "source": "cursor",
        "offset": 0,
        "limit": 1,
    }
    assert type(parameters["status"]).__name__ == "MemoryStatus"
    assert hashlib.sha256(database.read_bytes()).hexdigest() == before
    route = next(route for route in app.routes if isinstance(route, APIRoute) and route.path == URL)
    assert not inspect.iscoroutinefunction(route.endpoint)
    assert route.response_model is MemoryLibraryPage


def test_existing_home_summary_and_memories_array_semantics_are_preserved(app, database):
    app.state.memory_governance_store = MemoryGovernanceStore(database)
    app.state.source_read_access = SourceReadAccess(database)
    with closing(sqlite3.connect(database)) as db, db:
        put(db, "blocked", links=("e2",))
        put(db, "readable")
        grant(db, "workbuddy")
    old = request(app, url="/api/v1/memories", params={"limit": 1})
    assert old.status_code == 200
    assert old.json() == []  # Legacy post-pagination filtering is deliberately untouched.
    new = request(app, params={"limit": 1})
    assert new.status_code == 200
    assert new.json()["items"][0]["memory_id"] == "readable"
    summary = request(app, url="/api/v1/home-summary")
    assert summary.status_code == 200
    assert summary.json()["memory_count"] == 1
    assert summary.json()["latest"]["memory_id"] == "readable"


def test_openapi_exposes_only_read_route_with_bounds_and_paged_model(app):
    spec = app.openapi()
    operations = spec["paths"].get(URL, {})
    assert set(operations) == {"get"}
    operation = operations["get"]
    parameters = {parameter["name"]: parameter["schema"] for parameter in operation["parameters"]}
    assert set(parameters) == {"query", "status", "active_only", "source", "offset", "limit"}
    assert parameters["limit"]["minimum"] == 1
    assert parameters["limit"]["maximum"] == 500
    assert parameters["offset"]["maximum"] == 2**63 - 1
    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/MemoryLibraryPage"
    }
    page = spec["components"]["schemas"]["MemoryLibraryPage"]
    assert page["type"] == "object"
    assert page["additionalProperties"] is False
    assert set(page["required"]) == {"items", "total", "offset", "limit"}
    assert set(page["properties"]) == {
        "items",
        "total",
        "offset",
        "limit",
        "stale",
        "source_ids_by_memory",
        "source_time_by_memory",
    }
    assert page["properties"]["source_ids_by_memory"]["type"] == "object"
    assert page["properties"]["items"]["type"] == "array"
    assert page["properties"]["items"]["items"] == {"$ref": "#/components/schemas/GovernedMemory"}
    for field in ("total", "offset", "limit"):
        assert page["properties"][field]["type"] == "integer"
    assert page["properties"]["total"]["minimum"] == 0
    assert page["properties"]["offset"]["minimum"] == 0
    assert page["properties"]["limit"]["minimum"] == 1
    assert page["properties"]["limit"]["maximum"] == 500
    legacy = spec["paths"]["/api/v1/memories"]["get"]["responses"]["200"]
    legacy_schema = legacy["content"]["application/json"]["schema"]
    assert legacy_schema["type"] == "array"
    assert legacy_schema["items"] == {"$ref": "#/components/schemas/GovernedMemory"}
    assert request(app, method="POST").status_code == 405


def test_application_lifespan_wires_existing_database_and_shuts_down(tmp_path):
    async def run():
        home = tmp_path / "home"
        home.mkdir()
        application = api.create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=home,
                api_token=SecretStr("library-test-token"),
                automatic_memory_processing=False,
                live_sync_enabled=False,
            ),
            source_catalog={},
        )
        async with (
            application.router.lifespan_context(application),
            AsyncClient(
                transport=ASGITransport(app=application), base_url="http://test", headers=AUTH
            ) as client,
        ):
            empty = await client.get(URL)
            assert empty.status_code == 200
            assert empty.json()["total"] == 0
            path = application.state.evidence_store.database_path
            assert path == tmp_path / "data" / "one-memory.sqlite3"
            with closing(sqlite3.connect(path)) as db, db:
                put(db, "synthetic", links=())
            filled = await client.get(URL)
            assert filled.status_code == 200
            assert filled.json()["total"] == 1
            assert filled.json()["items"][0]["memory_id"] == "synthetic"
            assert (await client.get("/api/v1/home-summary")).json()["memory_count"] == 1

    asyncio.run(run())
