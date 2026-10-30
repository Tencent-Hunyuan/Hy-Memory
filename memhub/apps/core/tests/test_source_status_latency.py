"""Source status polling must not rediscover an already authorized history tree."""

import asyncio
import json
from time import perf_counter
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.access import source_root_identity
from one_memory.imports.models import EvidenceInput, EvidenceOrigin, OriginMetadata, WrittenBy
from one_memory.imports.sources.registry import build_source_catalog

SOURCES = ("workbuddy", "cursor", "claude-code", "codex")


def build_app(tmp_path, monkeypatch):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    home = tmp_path / "home"
    home.mkdir()
    catalog = build_source_catalog(home=home, environment={}, platform="darwin")
    for source_id in SOURCES:
        catalog[source_id].candidate.path.mkdir(parents=True)
    app = create_app(
        Settings(
            data_dir=tmp_path / "data",
            source_home=home,
            api_token=SecretStr("test"),
            automatic_memory_processing=False,
            live_sync_enabled=False,
            live_sync_watch_enabled=False,
        ),
        source_catalog=catalog,
    )
    return app, catalog


def client_for(app):
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer test"},
    )


def grant_source(app, registration):
    source_id = registration.descriptor.source_id
    store = app.state.source_grant_store
    receipt = store.grant(
        user_id="local",
        source_id=source_id,
        root=registration.candidate.path,
        candidate_kind=registration.candidate.kind,
        expected_root_id=source_root_identity(registration.candidate.path),
    )
    grant = store.authorized_root(user_id="local", source_id=source_id)
    assert grant is not None
    app.state.import_supervisor.register_adapter(registration.create_adapter(grant))
    return receipt


def seed_evidence(app, count=12_000):
    store = app.state.evidence_store
    with store._connect(writing=True) as db:
        for index in range(count):
            store.put(
                EvidenceInput(
                    source_id=SOURCES[index % len(SOURCES)],
                    conversation_id=f"conversation-{index // 40}",
                    message_id=f"message-{index}",
                    role="user",
                    written_by=WrittenBy.PERSON,
                    verbatim=f"Synthetic source status evidence {index}",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                ),
                connection=db,
            )


def seed_layouts(catalog, *, noise_files=256):
    """Native history layouts plus Cursor extension data preceding real history."""
    relative_paths = {
        "workbuddy": "project/session.jsonl",
        "cursor": "z-project/agent-transcripts/session/session.jsonl",
        "claude-code": "project/session.jsonl",
        "codex": "2026/09/14/rollout-session.jsonl",
    }
    for source_id, relative in relative_paths.items():
        target = catalog[source_id].candidate.path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"synthetic": True}) + "\n")
    cursor_root = catalog["cursor"].candidate.path
    for index in range(noise_files):
        target = cursor_root / "a-extension-data" / f"shard-{index // 16}" / f"{index}.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}\n")


async def source_status(client, source_id):
    response = await client.get("/api/v1/sources")
    assert response.status_code == 200, response.text
    return next(item for item in response.json() if item["source_id"] == source_id)


def test_large_import_status_does_not_scan_source_trees(tmp_path, monkeypatch):
    async def scenario():
        app, catalog = build_app(tmp_path, monkeypatch)
        seed_layouts(catalog)
        async with app.router.lifespan_context(app), client_for(app) as client:
            await app.state.recovery_task
            await app.state.memory_backfill
            await asyncio.to_thread(seed_evidence, app)
            probes = []
            for source_id in SOURCES:
                grant_source(app, catalog[source_id])
                adapter = app.state.import_supervisor.adapters[source_id]
                probe = AsyncMock(wraps=adapter.detect)
                monkeypatch.setattr(adapter, "detect", probe)
                probes.append(probe)

            start = perf_counter()
            results = await asyncio.gather(*(client.get("/api/v1/sources") for _ in range(4)))
            elapsed = perf_counter() - start
            for response in results:
                assert response.status_code == 200, response.text
                rows = {item["source_id"]: item for item in response.json()}
                for source_id in SOURCES:
                    assert rows[source_id]["authorized"] is True
                    assert rows[source_id]["imported_messages"] == 3_000
                    assert rows[source_id]["imported_conversations"] == 300
            assert sum(probe.await_count for probe in probes) == 0
            assert elapsed < 2.0
            assert app.state.source_detection == {}
            assert app.state.import_job_store.list_jobs() == []

    asyncio.run(scenario())


@pytest.mark.parametrize("source_id", SOURCES)
def test_status_keeps_explicit_detection_and_fresh_grant_and_sync_state(
    tmp_path, monkeypatch, source_id
):
    async def scenario():
        app, catalog = build_app(tmp_path, monkeypatch)
        seed_layouts(catalog, noise_files=0)
        async with app.router.lifespan_context(app), client_for(app) as client:
            registration = catalog[source_id]
            receipt = grant_source(app, registration)
            initial = await source_status(client, source_id)
            assert initial["authorized"] is True
            assert initial["detected"] is None
            assert initial["sync_enabled"] is False
            assert initial["grant_revision"] == receipt.revision == 1
            assert initial["candidate_change_reason"] == "source_candidate_locked"

            detected = await client.post(f"/api/v1/sources/{source_id}/detect")
            assert detected.status_code == 200
            assert detected.json()["detected"] is True
            assert (await source_status(client, source_id))["detected"] is True

            for enabled in (True, False):
                app.state.config_store.set_sync(source_id, enabled)
                status = await source_status(client, source_id)
                assert status["authorized"] is True
                assert status["sync_enabled"] is enabled

            revoked = await client.post(f"/api/v1/sources/{source_id}/revoke")
            assert revoked.status_code == 200
            status = await source_status(client, source_id)
            assert status["authorized"] is False
            assert status["sync_enabled"] is False
            assert status["detected"] is None
            assert status["candidate_change_allowed"] is False

            receipt = grant_source(app, registration)
            status = await source_status(client, source_id)
            assert status["authorized"] is True
            assert status["grant_revision"] == receipt.revision == 2
            assert status["sync_enabled"] is False

    asyncio.run(scenario())


@pytest.mark.parametrize("replacement", ["directory", "symlink", "missing"])
def test_status_rechecks_root_identity_even_with_warm_totals(tmp_path, monkeypatch, replacement):
    async def scenario():
        app, catalog = build_app(tmp_path, monkeypatch)
        async with app.router.lifespan_context(app), client_for(app) as client:
            registration = catalog["cursor"]
            receipt = grant_source(app, registration)
            app.state.config_store.set_sync("cursor", True)
            before = await source_status(client, "cursor")
            assert before["authorized"] is True and before["sync_enabled"] is True
            root = registration.candidate.path
            moved = root.with_name(root.name + "-previous")
            root.rename(moved)
            if replacement == "directory":
                root.mkdir()
            elif replacement == "symlink":
                root.symlink_to(moved, target_is_directory=True)
            after = await source_status(client, "cursor")
            assert after["authorized"] is False
            assert after["sync_enabled"] is False
            assert after["root_id"] == receipt.root_id
            assert after["grant_revision"] == receipt.revision
            assert after["candidate_root_id"] != receipt.root_id
            assert after["location_state"] == ("not_found" if replacement == "missing" else "found")

    asyncio.run(scenario())


def test_status_refreshes_selected_candidate_without_granting(tmp_path, monkeypatch):
    async def scenario():
        app, catalog = build_app(tmp_path, monkeypatch)
        selected = tmp_path / "selected"
        selected.mkdir()
        async with app.router.lifespan_context(app), client_for(app) as client:
            before = await source_status(client, "cursor")
            assert before["candidate_revision"] == 0
            result = await client.put(
                "/api/v1/sources/cursor/candidate",
                json={
                    "path": str(selected),
                    "expected_revision": 0,
                    "expected_root_id": source_root_identity(selected),
                    "user_confirmed": True,
                },
            )
            assert result.status_code == 200, result.text
            after = await source_status(client, "cursor")
            assert after["candidate_revision"] == 1
            assert after["candidate_kind"] == "user_selected"
            assert after["candidate_root_id"] == source_root_identity(selected)
            assert after["candidate_root_id"] != before["candidate_root_id"]
            assert after["authorized"] is False
            assert after["sync_enabled"] is False
            assert app.state.import_job_store.list_jobs() == []

    asyncio.run(scenario())


def test_revoke_during_pending_totals_cannot_return_cached_authorization(tmp_path, monkeypatch):
    async def scenario():
        app, catalog = build_app(tmp_path, monkeypatch)
        async with app.router.lifespan_context(app), client_for(app) as client:
            grant_source(app, catalog["cursor"])
            app.state.config_store.set_sync("cursor", True)
            before = await source_status(client, "cursor")
            assert before["authorized"] is True and before["sync_enabled"] is True
            started, release = asyncio.Event(), asyncio.Event()
            original = app.state.source_totals.by_source

            async def delayed_totals(*, user_id):
                started.set()
                await release.wait()
                return await original(user_id=user_id)

            monkeypatch.setattr(app.state.source_totals, "by_source", delayed_totals)
            pending = asyncio.create_task(source_status(client, "cursor"))
            try:
                await asyncio.wait_for(started.wait(), timeout=2)
                revoked = await client.post("/api/v1/sources/cursor/revoke")
                assert revoked.status_code == 200
            finally:
                release.set()
                after = await pending
            assert after["authorized"] is False
            assert after["sync_enabled"] is False
            assert after["grant_revision"] == before["grant_revision"]
            assert after["root_id"] == before["root_id"]

    asyncio.run(scenario())
