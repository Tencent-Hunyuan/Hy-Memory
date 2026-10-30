"""Permission execution regressions using temporary stores and Core ASGI instances."""

import asyncio
import json
from contextlib import asynccontextmanager

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.jobs import ImportStatus
from one_memory.imports.sources.registry import build_source_catalog
from one_memory.memory_server.config_state import ConfigStore
from one_memory.secrets import InMemorySecretStore
from one_memory.service_levels import ServiceLevel, ServiceResource
from tests.source_consent import authorized_request


@asynccontextmanager
async def isolated_core(tmp_path, monkeypatch):
    monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
    home = tmp_path / "home"
    root = home / ".claude/projects"
    root.mkdir(parents=True)
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        source_home=home,
        api_token=SecretStr("test"),
        imports_enabled=True,
        automatic_memory_processing=False,
        live_sync_enabled=True,
        live_sync_watch_enabled=False,
        live_sync_interval_seconds=3600,
    )
    app = create_app(
        settings,
        source_catalog=build_source_catalog(home=home, environment={}),
        secret_store=InMemorySecretStore(),
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": "Bearer test"},
        ) as client,
    ):
        yield app, client, root


def write_turn(root, identifier):
    path = root / f"{identifier}.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "user",
                "sessionId": identifier,
                "uuid": identifier,
                "message": {"content": identifier},
            }
        )
        + "\n"
    )
    return path


async def set_sync(client, receipt, *, enabled):
    del receipt
    response = await client.post(
        "/api/v1/sources/claude-code/sync-permission",
        json={"enabled": enabled},
    )
    assert response.status_code == 200, response.text
    assert response.json()["sync_enabled"] is enabled
    return response.json()


def test_root_regrant_cancels_old_pending_job_and_reads_only_the_new_root(tmp_path, monkeypatch):
    async def scenario():
        async with isolated_core(tmp_path, monkeypatch) as (app, client, root):
            authorized = await client.post(
                "/api/v1/sources/claude-code/authorize",
                json=await authorized_request(client, "claude-code"),
            )
            assert authorized.status_code == 200
            await set_sync(client, authorized.json(), enabled=True)
            # Opening sync schedules the initial history scan. Let that empty
            # scan finish before creating the old-root record used below.
            initial_jobs = [
                job
                for job in app.state.import_job_store.list_jobs()
                if job.source_id == "claude-code"
                and job.status in {"pending", "running", "interrupted"}
            ]
            for job in initial_jobs:
                await asyncio.wait_for(app.state.import_supervisor.wait(job.job_id), timeout=5)
            write_turn(root, "old-root-history")
            supervisor = app.state.import_supervisor
            hold = supervisor.service_levels.admit(
                ServiceResource.IMPORT,
                ServiceLevel.REALTIME,
                work_id="hold-old-root-job",
                scope_key="claude-code",
            )
            await hold.__aenter__()
            try:
                created = await client.post(
                    "/api/v1/import-jobs",
                    json={"source_id": "claude-code", "operation": "sync"},
                )
                assert created.status_code == 200
                stale = created.json()
                assert stale["status"] == "pending"
                root.rename(root.with_name("previous-projects"))
                root.mkdir()
                write_turn(root, "replacement-root-history")
                reauthorized = await client.post(
                    "/api/v1/sources/claude-code/authorize",
                    json=await authorized_request(client, "claude-code"),
                )
                assert reauthorized.status_code == 200
                assert reauthorized.json()["root_id"] != authorized.json()["root_id"]
                sources = (await client.get("/api/v1/sources")).json()
                current = next(item for item in sources if item["source_id"] == "claude-code")
                assert not current["sync_enabled"]
            finally:
                await hold.__aexit__(None, None, None)

            stopped = await asyncio.wait_for(supervisor.wait(stale["job_id"]), timeout=5)
            assert stopped.status == "cancelled"
            assert app.state.evidence_store.count() == 0
            await set_sync(client, reauthorized.json(), enabled=True)
            fresh = await client.post(
                "/api/v1/import-jobs",
                json={"source_id": "claude-code", "operation": "history"},
            )
            assert fresh.status_code == 200
            assert fresh.json()["job_id"] != stale["job_id"]
            assert fresh.json()["status"] == "pending"
            completed = await asyncio.wait_for(supervisor.wait(fresh.json()["job_id"]), timeout=5)
            assert completed.status == "completed"
            assert app.state.evidence_store.count() == 1
            records = app.state.evidence_store.list_extractable(limit=10)
            assert [record.verbatim for record in records] == ["replacement-root-history"]

    asyncio.run(scenario())


def test_root_regrant_waits_for_a_running_job(tmp_path, monkeypatch):
    async def scenario():
        async with isolated_core(tmp_path, monkeypatch) as (app, client, root):
            authorized = await client.post(
                "/api/v1/sources/claude-code/authorize",
                json=await authorized_request(client, "claude-code"),
            )
            assert authorized.status_code == 200
            running = app.state.import_job_store.create(
                user_id="local", source_id="claude-code", operation="sync"
            )
            claimed, _ = app.state.import_job_store.claim(running.job_id)
            assert claimed.status == "running"
            root.rename(root.with_name("previous-projects"))
            root.mkdir()

            refused = await client.post(
                "/api/v1/sources/claude-code/authorize",
                json=await authorized_request(client, "claude-code"),
            )

            assert refused.status_code == 409
            assert refused.json()["detail"] == "source_import_busy"
            current = app.state.source_grant_store.require_receipt(
                user_id="local", source_id="claude-code"
            )
            assert current.root_id == authorized.json()["root_id"]
            app.state.import_job_store.transition(running.job_id, ImportStatus.CANCELLED)

    asyncio.run(scenario())


def test_invalid_root_regrant_does_not_cancel_pending_work(tmp_path, monkeypatch):
    async def scenario():
        async with isolated_core(tmp_path, monkeypatch) as (app, client, _root):
            authorized = await client.post(
                "/api/v1/sources/claude-code/authorize",
                json=await authorized_request(client, "claude-code"),
            )
            assert authorized.status_code == 200
            pending = app.state.import_job_store.create(
                user_id="local", source_id="claude-code", operation="history"
            )

            refused = await client.post(
                "/api/v1/sources/claude-code/authorize",
                json={
                    "user_confirmed": True,
                    "expected_root_id": "root:v2:" + "0" * 64,
                },
            )

            assert refused.status_code == 409
            assert refused.json()["detail"] == "source_identity_changed"
            assert app.state.import_job_store.require(pending.job_id).status is ImportStatus.PENDING

    asyncio.run(scenario())


def test_sync_runtime_failure_does_not_commit_the_requested_state(tmp_path, monkeypatch):
    async def scenario():
        async with isolated_core(tmp_path, monkeypatch) as (app, client, _root):
            authorized = await client.post(
                "/api/v1/sources/claude-code/authorize",
                json=await authorized_request(client, "claude-code"),
            )
            assert authorized.status_code == 200

            resume = app.state.live_sync_supervisor.initialize_source

            async def fail_resume(*_args, **_kwargs):
                raise RuntimeError("resume_failed")

            monkeypatch.setattr(app.state.live_sync_supervisor, "initialize_source", fail_resume)
            failed_resume = await client.post(
                "/api/v1/sources/claude-code/sync-permission",
                json={"enabled": True},
            )
            assert failed_resume.status_code == 409
            assert failed_resume.json()["detail"] == "sync_prepare_failed"
            assert not app.state.config_store.sync_enabled("claude-code")

            monkeypatch.setattr(app.state.live_sync_supervisor, "initialize_source", resume)
            enabled = await client.post(
                "/api/v1/sources/claude-code/sync-permission",
                json={"enabled": True},
            )
            assert enabled.status_code == 200
            assert app.state.config_store.sync_enabled("claude-code")

            async def fail_pause(*_args, **_kwargs):
                raise RuntimeError("pause_failed")

            monkeypatch.setattr(app.state.live_sync_supervisor, "stop_source", fail_pause)
            failed_pause = await client.post(
                "/api/v1/sources/claude-code/sync-permission",
                json={"enabled": False},
            )
            assert failed_pause.status_code == 409
            assert failed_pause.json()["detail"] == "sync_prepare_failed"
            assert app.state.config_store.sync_enabled("claude-code")

    asyncio.run(scenario())


def test_startup_reconciles_enabled_source_from_config(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr("one_memory.model_presets._preset_data", lambda: None)
        home = tmp_path / "home"
        root = home / ".claude/projects"
        root.mkdir(parents=True)
        settings = Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            source_home=home,
            api_token=SecretStr("test"),
            imports_enabled=True,
            automatic_memory_processing=False,
            live_sync_enabled=True,
            live_sync_watch_enabled=False,
            live_sync_interval_seconds=3600,
        )
        catalog = build_source_catalog(home=home, environment={})
        first = create_app(settings, source_catalog=catalog, secret_store=InMemorySecretStore())
        async with (
            first.router.lifespan_context(first),
            AsyncClient(
                transport=ASGITransport(app=first),
                base_url="http://test",
                headers={"Authorization": "Bearer test"},
            ) as client,
        ):
            authorized = await client.post(
                "/api/v1/sources/claude-code/authorize",
                json=await authorized_request(client, "claude-code"),
            )
            assert authorized.status_code == 200

        write_turn(root, "arrived-while-core-was-down")
        ConfigStore(settings.data_dir).set_sync("claude-code", True)

        restored = create_app(
            settings,
            source_catalog=build_source_catalog(home=home, environment={}),
            secret_store=InMemorySecretStore(),
        )
        async with restored.router.lifespan_context(restored):
            # The reconcile runs after readiness, in recovery.
            await restored.state.recovery_task
            history = [
                job
                for job in restored.state.import_job_store.list_jobs()
                if job.source_id == "claude-code" and job.operation == "history"
            ]
            assert history, "startup must create the catch-up work described by config.json"
            for job in history:
                await asyncio.wait_for(restored.state.import_supervisor.wait(job.job_id), timeout=5)
            assert restored.state.evidence_store.count() == 1

    asyncio.run(scenario())
