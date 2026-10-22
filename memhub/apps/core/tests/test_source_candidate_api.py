"""Candidate location changes must not implicitly grant or import anything."""

import asyncio
import json
import sqlite3

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.access import source_root_identity
from one_memory.imports.sources.registry import build_source_catalog


def create_test_app(tmp_path, monkeypatch, environment=None):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    return create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=home,
            api_token=SecretStr("test"),
            automatic_memory_processing=False,
            live_sync_enabled=False,
            live_sync_watch_enabled=False,
        ),
        source_catalog=build_source_catalog(home=home, environment=environment or {}),
    )


def client_for(app):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer test"},
    )


def no_authority(app):
    assert app.state.import_job_store.list_jobs() == []
    with sqlite3.connect(app.state.evidence_store.database_path) as db:
        assert db.execute("SELECT COUNT(*) FROM source_grants").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM model_profiles").fetchone()[0] == 0


@pytest.mark.parametrize("source_id", ["workbuddy", "cursor", "claude-code", "codex"])
def test_custom_candidate_persists_without_authorizing_and_connects_exact_root(
    tmp_path, monkeypatch, source_id
):
    root = tmp_path / "custom-source"
    root.mkdir()
    text = "Isolated custom source budget<5"
    if source_id == "workbuddy":
        filename, record = (
            "session.jsonl",
            {
                "id": "1",
                "type": "message",
                "role": "user",
                "sessionId": "s",
                "content": [{"type": "input_text", "text": text}],
            },
        )
    elif source_id == "claude-code":
        filename, record = (
            "session.jsonl",
            {
                "type": "user",
                "uuid": "1",
                "sessionId": "s",
                "message": {"content": text},
            },
        )
    elif source_id == "codex":
        filename, record = (
            "rollout-session.jsonl",
            {
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "id": "1",
                    "content": [{"type": "input_text", "text": text}],
                },
            },
        )
    else:
        filename, record = (
            "agent-transcripts/s/s.jsonl",
            {
                "role": "user",
                "message": {"content": [{"type": "text", "text": text}]},
            },
        )
    file = root / filename
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(record) + "\n")

    async def scenario():
        app = create_test_app(tmp_path, monkeypatch)
        async with app.router.lifespan_context(app), client_for(app) as client:
            preview = await client.post(
                f"/api/v1/sources/{source_id}/candidate/preview", json={"path": str(root)}
            )
            assert preview.status_code == 200, preview.text
            no_authority(app)
            saved = await client.put(
                f"/api/v1/sources/{source_id}/candidate",
                json={
                    "path": preview.json()["path"],
                    "expected_revision": preview.json()["candidate_revision"],
                    "expected_root_id": preview.json()["root_id"],
                    "user_confirmed": True,
                },
            )
            assert saved.status_code == 200, saved.text
            revision = saved.json()["candidate_revision"]
            assert revision > 0
            no_authority(app)
        restarted = create_test_app(tmp_path, monkeypatch)
        async with restarted.router.lifespan_context(restarted), client_for(restarted) as client:
            sources = (await client.get("/api/v1/sources")).json()
            chosen = next(item for item in sources if item["source_id"] == source_id)
            assert chosen["candidate_kind"] == "user_selected"
            assert chosen["candidate_root_id"] == source_root_identity(root)
            assert chosen["candidate_revision"] == revision
            assert not chosen["authorized"]
            no_authority(restarted)
            body = {
                "source_ids": [source_id],
                "request_id": "custom-first",
                "source_roots": {source_id: chosen["candidate_root_id"]},
                "source_candidate_revisions": {source_id: revision},
                "confirm_environment_overrides": True,
                "enable_sync": True,
            }
            result = await client.post("/api/v1/sources/connect", json=body)
            assert result.status_code == 200, result.text
            assert result.json()["accepted_source_ids"] == [source_id], result.text
            job_id = result.json()["jobs"][0]["job_id"]
            await restarted.state.import_supervisor.wait(job_id)
            job = (await client.get(f"/api/v1/import-jobs/{job_id}")).json()
            assert job["status"] == "completed", job
            assert job["stored"] == 1 and job["errors"] == 0
            with sqlite3.connect(restarted.state.evidence_store.database_path) as db:
                assert db.execute("SELECT source_id,verbatim FROM evidence").fetchall() == [
                    (source_id, text)
                ]
            assert restarted.state.source_grant_store.require_receipt(
                user_id="local", source_id=source_id
            )
            assert restarted.state.config_store.sync_enabled(source_id)
            assert (await client.get("/api/v1/model-content-permission")).status_code == 404
            job_count = len(restarted.state.import_job_store.list_jobs())
            replay = await client.post("/api/v1/sources/connect", json=body)
            assert replay.json()["jobs"][0]["job_id"] == job_id
            assert len(restarted.state.import_job_store.list_jobs()) == job_count

    asyncio.run(scenario())


def test_custom_candidate_cannot_use_unversioned_or_legacy_authorize_entry(tmp_path, monkeypatch):
    root = tmp_path / "custom"
    root.mkdir()

    async def scenario():
        app = create_test_app(tmp_path, monkeypatch)
        async with app.router.lifespan_context(app), client_for(app) as client:
            saved = await client.put(
                "/api/v1/sources/cursor/candidate",
                json={
                    "path": str(root),
                    "expected_revision": 0,
                    "expected_root_id": source_root_identity(root),
                    "user_confirmed": True,
                },
            )
            assert saved.status_code == 200, saved.text
            legacy = await client.post(
                "/api/v1/sources/cursor/authorize",
                json={
                    "user_confirmed": True,
                    "expected_root_id": source_root_identity(root),
                    "confirm_environment_override": True,
                },
            )
            assert legacy.status_code == 409
            missing = await client.post(
                "/api/v1/sources/connect",
                json={
                    "source_ids": ["cursor"],
                    "request_id": "unversioned",
                    "source_roots": {"cursor": source_root_identity(root)},
                    "confirm_environment_overrides": True,
                },
            )
            assert missing.status_code in (200, 409)
            if missing.status_code == 200:
                assert missing.json()["accepted_source_ids"] == []
            no_authority(app)

    asyncio.run(scenario())


def test_custom_candidate_rejects_prior_source_and_replaced_root(tmp_path, monkeypatch):
    root = tmp_path / "custom"
    root.mkdir()

    async def scenario():
        app = create_test_app(tmp_path, monkeypatch)
        async with app.router.lifespan_context(app), client_for(app) as client:
            preview = await client.post(
                "/api/v1/sources/cursor/candidate/preview", json={"path": str(root)}
            )
            assert preview.status_code == 200, preview.text
            root.rename(root.with_name("previous"))
            root.mkdir()
            invalid = await client.put(
                "/api/v1/sources/cursor/candidate",
                json={
                    "path": str(root),
                    "expected_revision": 0,
                    "expected_root_id": preview.json()["root_id"],
                    "user_confirmed": True,
                },
            )
            assert invalid.status_code == 409
            no_authority(app)
            default = tmp_path / "home/.claude/projects"
            default.mkdir(parents=True)
            await client.post(
                "/api/v1/sources/claude-code/authorize",
                json={
                    "expected_root_id": source_root_identity(default),
                    "user_confirmed": True,
                },
            )
            await client.post("/api/v1/sources/claude-code/revoke")
            blocked = await client.post(
                "/api/v1/sources/claude-code/candidate/preview", json={"path": str(root)}
            )
            assert blocked.status_code == 409
            sources = (await client.get("/api/v1/sources")).json()
            assert not next(item for item in sources if item["source_id"] == "claude-code")[
                "candidate_change_allowed"
            ]

    asyncio.run(scenario())


@pytest.mark.parametrize("character", ["\n", "\r", "\t", "\x1b", "\x7f", "\x85"])
def test_candidate_rejects_existing_control_character_directory(tmp_path, monkeypatch, character):
    root = tmp_path / f"unsafe{character}directory"
    root.mkdir()

    async def scenario():
        app = create_test_app(tmp_path, monkeypatch)
        async with app.router.lifespan_context(app), client_for(app) as client:
            reply = await client.post(
                "/api/v1/sources/cursor/candidate/preview", json={"path": str(root)}
            )
            assert reply.status_code == 409
            assert reply.json()["detail"] == "source_candidate_invalid_path"
            no_authority(app)

    asyncio.run(scenario())
