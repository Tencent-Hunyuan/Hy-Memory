"""Add one memory directly: `POST /api/v2/memories` and `GET /api/v2/memories/additions/{id}`."""

import asyncio
import json
import sqlite3
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.models import WrittenBy
from one_memory.imports.storage import EvidenceStore
from one_memory.secrets import InMemorySecretStore
from one_memory.target_schema import legacy_tables_present


def application(tmp_path):
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path,
            api_token=SecretStr("memory-add-test"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        secret_store=InMemorySecretStore(),
    )


def legacy_application(tmp_path):
    data_dir = tmp_path / "data"
    EvidenceStore(data_dir / "one-memory.sqlite3")
    return application(tmp_path)


def client_for(app):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer memory-add-test"},
    )


@pytest.mark.fresh_sdk
def test_empty_library_can_add_a_memory_without_a_matter_or_configured_model(tmp_path):
    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            assert legacy_tables_present(app.state.settings.data_dir / "one-memory.sqlite3") == ()
            addition = {
                "request_id": str(uuid4()),
                "text": "请记住：项目周会不排在周五。",
                "scope": "all-apps",
            }
            response = await client.post("/api/v2/memories", json=addition)
            assert response.status_code == 201
            saved = response.json()
            assert saved["state"] == "pending"
            assert saved["memory_ids"] == []
            record = app.state.memory_addition_store.require(saved["evidence_id"])
            assert record.source_id == "one-memory-manual"
            assert record.role == "user" and record.written_by == WrittenBy.PERSON
            assert record.verbatim == addition["text"] and record.created_at is not None
            again = await client.post("/api/v2/memories", json=addition)
            assert again.json()["evidence_id"] == saved["evidence_id"]
            status = await client.get(f"/api/v2/memories/additions/{addition['request_id']}")
            assert status.status_code == 200
            assert status.json()["evidence_id"] == saved["evidence_id"]
            missing = await client.get(f"/api/v2/memories/additions/{uuid4()}")
            assert missing.status_code == 404
            assert missing.json()["detail"] == "memory_add_not_found"
            conflict = await client.post(
                "/api/v2/memories", json={**addition, "text": "Changed content"}
            )
            assert conflict.status_code == 409
            assert conflict.json()["detail"] == "memory_add_conflict"
            assert (
                app.state.memory_addition_store.require(saved["evidence_id"]).verbatim
                == addition["text"]
            )
            assert legacy_tables_present(app.state.settings.data_dir / "one-memory.sqlite3") == ()
        restarted = application(tmp_path)
        async with restarted.router.lifespan_context(restarted), client_for(restarted) as client:
            status = await client.get(f"/api/v2/memories/additions/{addition['request_id']}")
            assert status.status_code == 200
            assert status.json()["evidence_id"] == saved["evidence_id"]

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_memory_add_requires_authentication_explicit_scope_and_nonempty_content(tmp_path):
    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            base = {"request_id": str(uuid4()), "text": "Remember this.", "scope": "all-apps"}
            for change in (
                {"text": "  \n"},
                {"text": "x" * 12_001},
                {"scope": "*"},
                {"user_id": "other"},
            ):
                assert (
                    await client.post("/api/v2/memories", json={**base, **change})
                ).status_code == 422
            assert (
                await client.post(
                    "/api/v2/memories",
                    json={"request_id": base["request_id"], "text": "No scope"},
                )
            ).status_code == 422
            client.headers.pop("Authorization")
            assert (await client.post("/api/v2/memories", json=base)).status_code == 401
            status_path = f"/api/v2/memories/additions/{base['request_id']}"
            assert (await client.get(status_path)).status_code == 401
            client.headers["Authorization"] = "Bearer memory-add-test"
            assert (await client.get(status_path)).status_code == 404

    asyncio.run(scenario())


def test_memory_add_and_source_grant_are_atomic(tmp_path):
    from one_memory.memory_additions import MemoryAddInput

    async def scenario():
        app = legacy_application(tmp_path)
        async with app.router.lifespan_context(app):
            with sqlite3.connect(app.state.evidence_store.database_path) as db:
                db.execute(
                    "CREATE TRIGGER fail_manual_grant BEFORE INSERT ON source_grants "
                    "BEGIN SELECT RAISE(ABORT, 'grant failed'); END"
                )
            with pytest.raises(sqlite3.IntegrityError, match="grant failed"):
                app.state.memory_addition_store.save(
                    MemoryAddInput(
                        request_id=uuid4(), text="Do not leave half a save.", scope="all-apps"
                    )
                )
            assert app.state.evidence_store.count() == 0

    asyncio.run(scenario())


def test_revoked_manual_source_is_not_silently_reauthorized(tmp_path):
    async def scenario():
        app = legacy_application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            addition = {"request_id": str(uuid4()), "text": "Remember this.", "scope": "all-apps"}
            assert (await client.post("/api/v2/memories", json=addition)).status_code == 201
            with sqlite3.connect(app.state.evidence_store.database_path) as db:
                db.execute(
                    "UPDATE source_grants SET revoked_at='2026-09-12' "
                    "WHERE source_id='one-memory-manual'"
                )
            again = await client.post("/api/v2/memories", json=addition)
            assert again.status_code == 409
            assert again.json()["detail"] == "memory_add_source_revoked"
            status = await client.get(f"/api/v2/memories/additions/{addition['request_id']}")
            assert status.status_code == 409
            assert status.json()["detail"] == "memory_add_source_revoked"

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_sdk_manual_add_without_a_grant_row_is_claimed(tmp_path, monkeypatch):
    """The SDK add path writes no grant row; the claim gate used to require one.

    The intake admitted the job, the drain skipped it on every tick, and the
    note stayed Pending with attempts=0 while later jobs ran. A missing grant
    row is allowed; only an explicit revocation holds the job.
    """

    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            addition = {"request_id": str(uuid4()), "text": "Remember this.", "scope": "all-apps"}
            assert (await client.post("/api/v2/memories", json=addition)).status_code == 201
            database = app.state.settings.data_dir / "one-memory.sqlite3"
            with sqlite3.connect(database) as db:
                assert (
                    db.execute(
                        "SELECT COUNT(*) FROM source_grants WHERE source_id='one-memory-manual'"
                    ).fetchone()[0]
                    == 0
                )
                request_id = db.execute("SELECT request_id FROM memory_note_requests").fetchone()[0]
            job = app.state.memory_write_queue.get(request_id)
            assert job is not None and job.state == "pending"
            monkeypatch.setattr(
                app.state.model_profile_store, "active_identity", lambda: ("profile", 1)
            )

            assert app.state.memory_write_drain.claim(limit=1) == (job.job_id,)

            # An explicit revocation still holds the job and refuses new adds.
            app.state.memory_write_queue.settle(job.job_id, state="pending", owner=None)
            with sqlite3.connect(database) as db:
                db.execute(
                    "INSERT INTO source_grants (user_id,source_id,root_path,requested_path,"
                    "root_identity,candidate_kind,granted_at,revoked_at,revision) VALUES "
                    "('local','one-memory-manual','x','x','manual','default',"
                    "'2026-10-01T00:00:00+00:00','2026-10-27T00:00:00+00:00',1)"
                )
            assert app.state.memory_write_drain.claim(limit=1) == ()
            refused = await client.post(
                "/api/v2/memories",
                json={**addition, "request_id": str(uuid4())},
            )
            assert refused.status_code == 409
            assert refused.json()["detail"] == "memory_add_source_revoked"

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_sdk_memory_write_accepts_openai_messages_without_evidence_tables(tmp_path):
    async def scenario():
        app = application(tmp_path)
        request_id = str(uuid4())
        body = {
            "request_id": request_id,
            "session_id": "cli-session",
            "source_id": "memhub-cli",
            "messages": [
                {"role": "user", "content": "Remember the release is Tuesday."},
                {"role": "assistant", "content": "I will remember that."},
            ],
        }
        async with app.router.lifespan_context(app), client_for(app) as client:
            response = await client.post("/api/v2/memory-writes", json=body)
            assert response.status_code == 202, response.text
            assert response.json()["request_id"] == request_id
            replay = await client.post("/api/v2/memory-writes", json=body)
            assert replay.status_code == 202
            assert replay.json()["detail"] == "already accepted; not enqueued again"
            conflict = await client.post(
                "/api/v2/memory-writes",
                json={
                    **body,
                    "messages": [{"role": "user", "content": "Different content."}],
                },
            )
            assert conflict.status_code == 409
            status = await client.get(f"/api/v2/memory-writes/{request_id}")
            assert status.status_code == 200
            with sqlite3.connect(app.state.settings.data_dir / "one-memory.sqlite3") as database:
                payload = database.execute(
                    "SELECT payload_json FROM memory_write_jobs WHERE idempotency_key=?",
                    (request_id,),
                ).fetchone()[0]
            frozen = json.loads(payload)
            assert [item["verbatim"] for item in frozen["input_records"]] == [
                "Remember the release is Tuesday.",
                "I will remember that.",
            ]
            assert legacy_tables_present(app.state.settings.data_dir / "one-memory.sqlite3") == ()

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_sdk_user_profile_http_returns_native_assembled_profile(tmp_path):
    class Bridge:
        def get_user_profile(self, *, user_id: str):
            assert user_id == "local"
            return {
                "request_id": "profile-request",
                "profile": {
                    "memory_id": "l0-head",
                    "content": "name is Alice\noccupation is Engineer",
                    "layer": "L0_BASIC_INFO",
                    "basic_info": {"name": "Alice", "occupation": "Engineer"},
                },
                "elapsed_ms": 1.25,
            }

    class Handle:
        async def client(self):
            return Bridge()

        async def close(self):
            return None

    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            app.state.sdk_client_handle = Handle()
            response = await client.get("/api/v2/user-profile")
            assert response.status_code == 200, response.text
            assert response.json() == {
                "request_id": "profile-request",
                "profile": {
                    "memory_id": "l0-head",
                    "content": "name is Alice\noccupation is Engineer",
                    "layer": "L0_BASIC_INFO",
                    "basic_info": {"name": "Alice", "occupation": "Engineer"},
                },
                "elapsed_ms": 1.25,
            }

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_sdk_memory_library_uses_sdk_search_and_skips_empty_rows_without_mutation(
    tmp_path,
    caplog,
):
    async def scenario():
        app = application(tmp_path)
        list_calls = []
        search_calls = []
        deleted = []
        records = {
            "release": {
                "memory_id": "release",
                "content": "Release plan",
                "layer": "l2_fact",
                "status": "active",
                "user_id": "local",
                "agent_id": "all-apps",
                "session_id": "s1",
                "source": "cursor",
                "tags": ["shipping"],
            },
            "retired": {
                "memory_id": "retired",
                "content": "Release plan, first draft",
                "layer": "l2_fact",
                "status": "superseded",
                "user_id": "local",
                "agent_id": "all-apps",
                "session_id": "s1",
                "source": "cursor",
                "tags": [],
            },
            "default": {
                "memory_id": "default",
                "content": "Unknown source",
                "layer": "l2_fact",
                "status": "active",
                "user_id": "local",
                "agent_id": "all-apps",
                "session_id": "s3",
                "source": "default",
                "tags": [],
            },
        }

        class Client:
            def list_memories(self, **kwargs):
                list_calls.append(kwargs)
                rows = [
                    records["release"],
                    records["default"],
                    {
                        "memory_id": "empty",
                        "content": "",
                        "layer": "l2_fact",
                        "status": "active",
                        "user_id": "local",
                        "agent_id": "all-apps",
                        "session_id": "s2",
                        "source": "workbuddy",
                        "tags": [],
                    },
                ]
                include_sources = kwargs.get("include_sources") or []
                if include_sources:
                    rows = [row for row in rows if row["source"] in include_sources]
                if kwargs.get("scene") == "all":
                    return {
                        "scene": "all",
                        "memories": [{**row, "scene": "chat", "kind": "chat"} for row in rows],
                        "total": len(rows),
                        "totals": {"chat": len(rows), "working": 0, "coding": 0},
                        "errors": {},
                        "limit": kwargs["limit"],
                        "offset": kwargs["offset"],
                    }
                return {
                    "vdb": {
                        "memories": rows,
                        "total": len(rows),
                        "limit": kwargs["limit"],
                        "offset": kwargs["offset"],
                    }
                }

            def search(self, query, **kwargs):
                search_calls.append((query, kwargs))
                return {
                    "scene": "all",
                    "memories": [
                        {
                            "memory_id": "release",
                            "content": "Release plan",
                            "score": 0.9,
                            "scene": "chat",
                            "kind": "chat",
                        },
                        {
                            "memory_id": "retired",
                            "content": "Release plan, first draft",
                            "score": 0.8,
                            "scene": "chat",
                            "kind": "chat",
                        },
                    ],
                    "lanes": {"chat": 2, "working": 0, "coding": 0},
                    "errors": {},
                }

            def get(self, memory_id, **_namespace):
                return records.get(memory_id)

            def delete(self, memory_id):
                deleted.append(memory_id)
                return {"success": True, "deleted_count": 1}

        sdk = Client()

        class Handle:
            async def client(self):
                return sdk

            async def close(self):
                return None

        async with app.router.lifespan_context(app):
            app.state.sdk_client_handle = Handle()
            async with client_for(app) as client:
                response = await client.get(
                    "/api/v2/memory-library",
                    params={"source": "cursor", "query": "release"},
                )
                assert response.status_code == 200
                body = response.json()
                # `total` counts the window's keyword matches; the superseded
                # hit matches but is not listed as an active memory.
                assert body["total"] == 2
                assert body["search_window"] is None
                assert [item["memory_id"] for item in body["memories"]] == ["release"]
                assert search_calls[0][0] == "release"
                assert search_calls[0][1]["include_sources"] == ["cursor"]
                assert search_calls[0][1]["limit"] == 200
                assert search_calls[0][1]["scene"] == "all"
                assert search_calls[0][1]["rerank"] is False
                assert search_calls[0][1]["intention_limit"] == 0
                assert search_calls[0][1]["min_score"] == 0.0
                assert list_calls == []

                # The SDK still returns its nearest rows for a word that is in
                # none of them; the library reports no match instead.
                unmatched = await client.get(
                    "/api/v2/memory-library", params={"query": "zzz nowhere"}
                )
                assert unmatched.json()["total"] == 0
                assert unmatched.json()["memories"] == []

                listed = await client.get(
                    "/api/v2/memory-library",
                    params={"source": "workbuddy", "limit": 100},
                )
                assert listed.status_code == 200
                assert listed.json()["memories"] == []
                assert listed.json()["total"] == 1
                assert deleted == []
                assert list_calls[0]["limit"] == 100
                assert list_calls[0]["scene"] == "all"
                assert "sdk_memory_invalid_rows_skipped count=1" in caplog.messages

                graph = await client.get("/api/v2/memories/graph")
                assert graph.status_code == 200
                node_ids = {node["node_id"] for node in graph.json()["nodes"]}
                assert "source:cursor" in node_ids
                assert "source:default" not in node_ids

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_sdk_context_search_sets_each_recall_lane_apart(tmp_path):
    """Normal, schema and intention lanes default to 10 / 3 / 1 and pass through."""

    async def scenario():
        app = application(tmp_path)
        calls = []

        class Client:
            def search(self, query, **kwargs):
                calls.append(kwargs)
                return {"memories": {"schema": [], "proactive": [], "normal": []}}

            def get(self, memory_id):
                return None

        sdk = Client()

        class Handle:
            async def client(self):
                return sdk

            async def close(self):
                return None

        async with app.router.lifespan_context(app):
            app.state.sdk_client_handle = Handle()
            async with client_for(app) as client:
                defaulted = await client.post(
                    "/api/v2/context/search",
                    json={"query": "release", "kinds": ["memory"]},
                )
                chosen = await client.post(
                    "/api/v2/context/search",
                    json={
                        "query": "release",
                        "kinds": ["memory"],
                        "limit": 5,
                        "schema_limit": 0,
                        "intention_limit": 2,
                    },
                )
        assert defaulted.status_code == 200
        assert chosen.status_code == 200
        assert [
            (call["limit"], call["schema_limit"], call["intention_limit"]) for call in calls
        ] == [(10, 3, 1), (5, 0, 2)]

    asyncio.run(scenario())


def _envelope(tools=("om_recall", "om_ask")) -> dict[str, str]:
    return {
        "x-one-memory-read-context": "external",
        "x-one-memory-installation-id": "00000000-0000-0000-0000-000000000001",
        "x-one-memory-installation-revision": "1",
        "x-one-memory-allowed-scopes": json.dumps(["all-apps"]),
        "x-one-memory-allowed-tools": json.dumps(list(tools)),
    }


def _bare_request(method: str, path: str, headers: dict[str, str]):
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "headers": [(k.encode(), v.encode()) for k, v in headers.items()],
        }
    )


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v2/memories"),
        ("GET", f"/api/v2/memories/additions/{uuid4()}"),
    ],
)
def test_memory_add_routes_are_denied_to_enveloped_requests(method, path):
    """The broker writes as a trusted local caller; an external envelope never
    reaches the add or status route, whatever tools it carries."""
    from fastapi import HTTPException

    from one_memory.external_memory_access import TOOLS, require_external_endpoint

    with pytest.raises(HTTPException) as error:
        require_external_endpoint(_bare_request(method, path, _envelope(sorted(TOOLS))))
    assert (error.value.status_code, error.value.detail) == (403, "external_endpoint_denied")
    # A trusted local caller (no envelope) is not refused by this gate.
    require_external_endpoint(_bare_request(method, path, {}))


def test_existing_memories_broker_routes_keep_their_access_rules():
    """Adding `POST /api/v2/memories` neither opens nor closes the ask routes."""
    from fastapi import HTTPException

    from one_memory.external_memory_access import require_external_endpoint

    for path in ("/api/v2/memories/ask", "/api/v2/memories/ask/stream"):
        require_external_endpoint(_bare_request("POST", path, _envelope(("om_ask",))))
        with pytest.raises(HTTPException) as denied:
            require_external_endpoint(_bare_request("POST", path, _envelope(("om_recall",))))
        assert denied.value.detail == "external_recall_scope_denied"
    for method, path in (
        ("GET", "/api/v2/memories/some-memory"),
        ("GET", "/api/v2/memories/graph"),
        ("PUT", "/api/v2/memories/some-memory"),
        ("DELETE", "/api/v2/memories/some-memory"),
    ):
        with pytest.raises(HTTPException) as denied:
            require_external_endpoint(_bare_request(method, path, _envelope(("om_ask",))))
        assert denied.value.detail == "external_endpoint_denied"


@pytest.mark.fresh_sdk
def test_enveloped_memory_add_saves_nothing(tmp_path):
    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            addition = {"request_id": str(uuid4()), "text": "Remember this.", "scope": "all-apps"}
            refused = await client.post("/api/v2/memories", json=addition, headers=_envelope())
            assert refused.status_code == 403
            assert refused.json()["detail"] == "external_endpoint_denied"
            status = f"/api/v2/memories/additions/{addition['request_id']}"
            assert (await client.get(status, headers=_envelope())).status_code == 403
            assert (await client.get(status)).status_code == 404

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_memory_add_routes_do_not_collide_with_single_memory_routes(tmp_path):
    """`/additions/{request_id}` is two segments and the add is a POST on the
    collection: neither may be served by `/api/v2/memories/{memory_id}`."""
    sdk_calls: list[tuple[str, object]] = []

    class Client:
        def __getattr__(self, name):
            def record(*args, **kwargs):
                sdk_calls.append((name, args))
                return None

            return record

    class Handle:
        async def client(self):
            return Client()

        async def close(self):
            return None

    async def scenario():
        app = application(tmp_path)
        request_id = str(uuid4())
        async with app.router.lifespan_context(app), client_for(app) as client:
            app.state.sdk_client_handle = Handle()
            missing = await client.get(f"/api/v2/memories/additions/{request_id}")
            assert missing.status_code == 404
            assert missing.json()["detail"] == "memory_add_not_found"
            created = await client.post(
                "/api/v2/memories",
                json={"request_id": request_id, "text": "Remember this.", "scope": "all-apps"},
            )
            assert created.status_code == 201
            assert set(created.json()) == {
                "evidence_id",
                "text",
                "scope",
                "created_at",
                "state",
                "task_id",
                "memory_ids",
                "error",
            }
            status = await client.get(f"/api/v2/memories/additions/{request_id}")
            assert status.json()["evidence_id"] == created.json()["evidence_id"]
            # Neither route reached the single-memory SDK reads ...
            assert not [call for call in sdk_calls if call[0] == "get"]
            # ... while a single segment still does, even one named `additions`.
            await client.get("/api/v2/memories/additions")
            await client.get("/api/v2/memories/some-memory")
            assert [call[1][0] for call in sdk_calls if call[0] == "get"] == [
                "additions",
                "some-memory",
            ]
            # The old note surface is gone.
            assert (await client.get("/api/v1/memory-notes")).status_code == 404
            assert (
                await client.post(
                    "/api/v1/memory-notes",
                    json={"request_id": str(uuid4()), "text": "x", "scope": "all-apps"},
                )
            ).status_code == 404
            schema = (await client.get("/openapi.json")).json()
            assert "MemoryAddReceipt" in schema["components"]["schemas"]
            assert "MemoryAddInput" in schema["components"]["schemas"]
            assert not [name for name in schema["components"]["schemas"] if "Note" in name]
            assert not [path for path in schema["paths"] if "note" in path]

    asyncio.run(scenario())
