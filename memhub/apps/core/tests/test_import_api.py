import asyncio
import json

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.imports.jobs import ImportStatus, InvalidJobTransitionError
from one_memory.imports.sources.registry import build_source_catalog
from tests.source_consent import authorized_request, connection_request


def test_resume_during_cleanup_returns_conflict_without_internal_error(tmp_path, monkeypatch):
    async def scenario():
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path / "home",
                api_token=SecretStr("synthetic"),
                automatic_memory_processing=False,
                live_sync_enabled=False,
            )
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer synthetic"},
            ) as client,
        ):
            supervisor = app.state.import_supervisor
            job = supervisor.job_store.create(user_id="local", source_id="fixture")
            supervisor.job_store.transition(job.job_id, ImportStatus.PAUSED)

            async def draining(_job_id):
                raise InvalidJobTransitionError("import_cleanup_in_progress")

            monkeypatch.setattr(supervisor, "resume_off_loop", draining)
            response = await client.post(f"/api/v1/import-jobs/{job.job_id}/resume")
            assert response.status_code == 409
            assert response.json() == {"detail": "invalid_job_transition"}
            assert supervisor.job_store.require(job.job_id).status is ImportStatus.PAUSED

    asyncio.run(scenario())


def test_authorize_detect_import_and_revoke_api_flow(tmp_path) -> None:
    async def scenario() -> None:
        home = tmp_path / "home"
        projects = home / ".claude" / "projects"
        projects.mkdir(parents=True)
        (projects / "session.jsonl").write_text(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "session",
                    "uuid": "message",
                    "message": {"content": "Remember this"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        settings = Settings(
            data_dir=tmp_path / "data",
            source_home=home,
            api_token=SecretStr("test-token"),
        )
        catalog = build_source_catalog(
            home=home,
            environment={},
            platform="darwin",
        )
        app = create_app(settings, source_catalog=catalog)
        headers = {
            "Authorization": "Bearer test-token",
            "Origin": "http://127.0.0.1:5173",
        }

        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers=headers,
            ) as client,
        ):
            sources = await client.get("/api/v1/sources")
            assert sources.status_code == 200
            # Whatever the catalog registers, not a number kept in step by
            # hand: the count changed the day Qwen Code stopped being mistaken
            # for Qwen Work and got its own adapter.
            assert len(sources.json()) == len(catalog)
            claude = next(item for item in sources.json() if item["source_id"] == "claude-code")
            assert claude["authorized"] is False
            assert claude["detected"] is None

            authorization = await client.post(
                "/api/v1/sources/claude-code/authorize",
                json=await authorized_request(client, "claude-code"),
            )
            assert authorization.status_code == 200
            assert str(home) not in authorization.text

            detection = await client.post("/api/v1/sources/claude-code/detect")
            assert detection.json()["detected"] is True

            thawed = await client.post(
                "/api/v1/sources/claude-code/sync-permission",
                json={"enabled": True},
            )
            assert thawed.status_code == 200, thawed.text

            created = await client.post(
                "/api/v1/import-jobs",
                json={"source_id": "claude-code"},
            )
            assert created.status_code == 200
            job_id = created.json()["job_id"]
            completed = await app.state.import_supervisor.wait(job_id)
            assert completed.status == "completed"
            assert completed.stored == 1

            jobs = await client.get("/api/v1/import-jobs")
            assert jobs.status_code == 200
            assert jobs.json()[0]["status"] == "completed"

            revoked = await client.post("/api/v1/sources/claude-code/revoke")
            assert revoked.status_code == 200
            assert revoked.json()["revoked_at"] is not None

    asyncio.run(scenario())


def test_protected_api_rejects_missing_token_and_foreign_origin(tmp_path) -> None:
    async def scenario() -> None:
        settings = Settings(
            data_dir=tmp_path / "data",
            source_home=tmp_path / "home",
            api_token=SecretStr("test-token"),
        )
        app = create_app(settings, source_catalog={})
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as client,
        ):
            missing = await client.get("/api/v1/sources")
            foreign = await client.get(
                "/api/v1/sources",
                headers={
                    "Authorization": "Bearer test-token",
                    "Origin": "https://malicious.example",
                },
            )

        assert missing.status_code == 401
        assert foreign.status_code == 403

    asyncio.run(scenario())


def test_one_consent_connects_and_starts_multiple_sources(tmp_path) -> None:
    async def scenario() -> None:
        home = tmp_path / "home"
        claude = home / ".claude" / "projects" / "session.jsonl"
        workbuddy = home / ".workbuddy" / "projects" / "session.jsonl"
        claude.parent.mkdir(parents=True)
        workbuddy.parent.mkdir(parents=True)
        claude.write_text(
            json.dumps(
                {
                    "type": "user",
                    "sessionId": "claude-session",
                    "uuid": "claude-message",
                    "message": {"content": "Remember Claude"},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        workbuddy.write_text(
            json.dumps(
                {
                    "type": "message",
                    "role": "user",
                    "sessionId": "workbuddy-session",
                    "id": "workbuddy-message",
                    "content": [{"type": "input_text", "text": "Remember WorkBuddy"}],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        settings = Settings(
            data_dir=tmp_path / "data",
            source_home=home,
            api_token=SecretStr("test-token"),
        )
        app = create_app(
            settings,
            source_catalog=build_source_catalog(
                home=home,
                environment={},
                platform="darwin",
            ),
        )
        headers = {
            "Authorization": "Bearer test-token",
            "Origin": "http://127.0.0.1:5173",
        }
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers=headers,
            ) as client,
        ):
            response = await client.post(
                "/api/v1/sources/connect",
                json=await connection_request(
                    client,
                    ["claude-code", "workbuddy"],
                    full_rescan=True,
                    onboarding=True,
                    enable_sync=True,
                ),
            )
            assert response.status_code == 200
            payload = response.json()
            assert payload["accepted_source_ids"] == ["claude-code", "workbuddy"]
            onboarding = (await client.get("/api/v1/onboarding")).json()
            assert onboarding["status"] == "importing"
            assert onboarding["selected_source_ids"] == ["claude-code", "workbuddy"]
            completed = [
                await app.state.import_supervisor.wait(job["job_id"]) for job in payload["jobs"]
            ]
            assert [job.stored for job in completed] == [1, 1]
            assert app.state.evidence_store.count() == 2
            sources = (await client.get("/api/v1/sources")).json()
            workbuddy_source = next(item for item in sources if item["source_id"] == "workbuddy")
            assert workbuddy_source["imported_messages"] == 1
            assert workbuddy_source["imported_conversations"] == 1
            finished = await client.post("/api/v1/onboarding/complete")
            assert finished.json()["status"] == "completed"
            assert finished.json()["completed_at"] is not None

    asyncio.run(scenario())


def test_environment_override_requires_explicit_confirmation_and_shows_path(
    tmp_path,
) -> None:
    async def scenario() -> None:
        home = tmp_path / "home"
        custom = tmp_path / "custom-claude"
        (custom / "projects").mkdir(parents=True)
        settings = Settings(
            data_dir=tmp_path / "data",
            source_home=home,
            api_token=SecretStr("test-token"),
        )
        catalog = build_source_catalog(
            home=home,
            environment={"CLAUDE_CONFIG_DIR": str(custom)},
            platform="darwin",
        )
        app = create_app(settings, source_catalog=catalog)
        headers = {
            "Authorization": "Bearer test-token",
            "Origin": "http://127.0.0.1:5173",
        }
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers=headers,
            ) as client,
        ):
            sources = (await client.get("/api/v1/sources")).json()
            claude = next(item for item in sources if item["source_id"] == "claude-code")
            assert claude["candidate_path"] == str(custom / "projects")
            assert claude["capabilities"]["verification"] == "real_replayed"
            assert claude["capabilities"]["live_sync"] == "implemented"
            # A live connection here follows additions only; the UI has to be
            # able to say so instead of implying the copy tracks the source.
            assert claude["sync_completeness"] == "additions_only"

            rejected = await client.post(
                "/api/v1/sources/claude-code/authorize",
                json=await authorized_request(client, "claude-code"),
            )
            assert rejected.status_code == 409

            accepted = await client.post(
                "/api/v1/sources/claude-code/authorize",
                json=await authorized_request(
                    client, "claude-code", confirm_environment_override=True
                ),
            )
            assert accepted.status_code == 200

    asyncio.run(scenario())
