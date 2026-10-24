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
            api_token=SecretStr("note-test"),
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
        headers={"Authorization": "Bearer note-test"},
    )


@pytest.mark.fresh_sdk
def test_empty_library_can_save_a_note_without_a_matter_or_configured_model(tmp_path):
    async def scenario():
        app = application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            assert legacy_tables_present(app.state.settings.data_dir / "one-memory.sqlite3") == ()
            note = {
                "request_id": str(uuid4()),
                "text": "请记住：项目周会不排在周五。",
                "scope": "all-apps",
            }
            response = await client.post("/api/v1/memory-notes", json=note)
            assert response.status_code == 201
            saved = response.json()
            assert saved["state"] == "pending"
            assert saved["memory_ids"] == []
            record = app.state.memory_note_store.require(saved["evidence_id"])
            assert record.source_id == "one-memory-manual"
            assert record.role == "user" and record.written_by == WrittenBy.PERSON
            assert record.verbatim == note["text"] and record.created_at is not None
            again = await client.post("/api/v1/memory-notes", json=note)
            assert again.json()["evidence_id"] == saved["evidence_id"]
            operation = await client.get(f"/api/v1/memory-notes/operations/{note['request_id']}")
            assert operation.status_code == 200
            assert operation.json()["evidence_id"] == saved["evidence_id"]
            assert (
                await client.get(f"/api/v1/memory-notes/operations/{uuid4()}")
            ).status_code == 404
            conflict = await client.post(
                "/api/v1/memory-notes", json={**note, "text": "Changed content"}
            )
            assert conflict.status_code == 409
            assert (
                app.state.memory_note_store.require(saved["evidence_id"]).verbatim == note["text"]
            )
            retired = await client.get(f"/api/v1/memory-notes/{saved['evidence_id']}/evidence")
            assert retired.status_code == 409
            assert retired.json()["detail"] == "memory_note_evidence_retired"
            assert legacy_tables_present(app.state.settings.data_dir / "one-memory.sqlite3") == ()
        restarted = application(tmp_path)
        async with restarted.router.lifespan_context(restarted), client_for(restarted) as client:
            notes = (await client.get("/api/v1/memory-notes")).json()
            assert len(notes) == 1 and notes[0]["evidence_id"] == saved["evidence_id"]

    asyncio.run(scenario())


@pytest.mark.fresh_sdk
def test_note_requires_authentication_explicit_scope_and_nonempty_content(tmp_path):
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
                    await client.post("/api/v1/memory-notes", json={**base, **change})
                ).status_code == 422
            assert (
                await client.post(
                    "/api/v1/memory-notes",
                    json={"request_id": base["request_id"], "text": "No scope"},
                )
            ).status_code == 422
            client.headers.pop("Authorization")
            assert (await client.post("/api/v1/memory-notes", json=base)).status_code == 401
            assert app.state.memory_note_store.list() == []

    asyncio.run(scenario())


def test_saved_note_evidence_returns_raw_and_consent_reference_from_same_record(tmp_path):
    async def scenario():
        app = legacy_application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            text = "Original  spacing\n预算<5，不能改为>5。"
            saved = (
                await client.post(
                    "/api/v1/memory-notes",
                    json={
                        "request_id": str(uuid4()),
                        "text": text,
                        "scope": "all-apps",
                    },
                )
            ).json()
            endpoint = f"/api/v1/memory-notes/{saved['evidence_id']}/evidence"
            response = await client.get(endpoint)
            assert response.status_code == 200, response.text
            value = response.json()
            assert value["verbatim"] == text
            assert value["evidence_id"] == saved["evidence_id"] and value["revision"] == 1
            assert value["role"] == "user" and value["written_by"] == "person"
            assert value["available"] is True and value["source_id"] == "one-memory-manual"
            assert app.state.memory_task_store.list() == []
            assert (await client.get("/api/v1/memory-notes/missing/evidence")).status_code == 404
            client.headers.pop("Authorization")
            assert (await client.get(endpoint)).status_code == 401

    asyncio.run(scenario())


def test_note_and_source_grant_are_atomic(tmp_path):
    from one_memory.memory_notes import MemoryNoteInput

    async def scenario():
        app = legacy_application(tmp_path)
        async with app.router.lifespan_context(app):
            with sqlite3.connect(app.state.evidence_store.database_path) as db:
                db.execute(
                    "CREATE TRIGGER fail_manual_grant BEFORE INSERT ON source_grants "
                    "BEGIN SELECT RAISE(ABORT, 'grant failed'); END"
                )
            with pytest.raises(sqlite3.IntegrityError, match="grant failed"):
                app.state.memory_note_store.save(
                    MemoryNoteInput(
                        request_id=uuid4(), text="Do not leave half a save.", scope="all-apps"
                    )
                )
            assert app.state.evidence_store.count() == 0

    asyncio.run(scenario())


def test_revoked_note_source_is_not_silently_reauthorized(tmp_path):
    async def scenario():
        app = legacy_application(tmp_path)
        async with app.router.lifespan_context(app), client_for(app) as client:
            note = {"request_id": str(uuid4()), "text": "Remember this.", "scope": "all-apps"}
            saved = (await client.post("/api/v1/memory-notes", json=note)).json()
            with sqlite3.connect(app.state.evidence_store.database_path) as db:
                db.execute(
                    "UPDATE source_grants SET revoked_at='2026-09-12' "
                    "WHERE source_id='one-memory-manual'"
                )
            assert (await client.post("/api/v1/memory-notes", json=note)).status_code == 409
            assert (
                await client.post(f"/api/v1/memory-notes/{saved['evidence_id']}/process", json={})
            ).status_code == 409
            assert (await client.get("/api/v1/memory-notes")).json() == []

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
