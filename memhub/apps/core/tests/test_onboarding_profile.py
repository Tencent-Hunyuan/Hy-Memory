"""Onboarding endpoints manage UI state without touching SDK memory."""

import asyncio
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.onboarding import OnboardingStore


def _app(tmp_path: Path):
    return create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            source_home=tmp_path / "home",
            api_token=SecretStr("test-token"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )


def _client(app) -> AsyncClient:
    return AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={
            "Authorization": "Bearer test-token",
            "Origin": "http://127.0.0.1:5173",
        },
    )


def test_finishing_onboarding_is_not_model_content_consent(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app), _client(app) as client:
            calls: list[str] = []

            async def digest() -> dict[str, object]:
                calls.append("digest")
                return {"success": True}

            app.state.sdk_memory_runtime.digest = digest
            finished = await client.post("/api/v1/onboarding/complete")

            assert finished.status_code == 200
            assert finished.json()["status"] == "completed"
            assert calls == []

    asyncio.run(scenario())


def test_reset_returns_to_first_run_without_touching_sdk_data(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app), _client(app) as client:
            sdk_marker = tmp_path / "data" / "hy-memory" / "governed-v2" / "memory.marker"
            sdk_marker.parent.mkdir(parents=True, exist_ok=True)
            sdk_marker.write_text("keep-sdk-memory", encoding="utf-8")
            progress = await client.patch(
                "/api/v1/onboarding/progress",
                json={
                    "mode_intent": "pro",
                    "setup_step": "model",
                    "model_profile_id": "9be6d465-4cf2-4c72-93f7-15f3c012bc80",
                },
            )
            assert progress.status_code == 200

            completed = await client.post("/api/v1/onboarding/complete")
            assert completed.status_code == 200
            assert completed.json()["mode_intent"] is None
            assert completed.json()["setup_step"] is None
            assert completed.json()["model_profile_id"] is None
            reset = await client.post("/api/v1/onboarding/reset")

            assert reset.status_code == 200
            assert reset.json()["status"] == "not_started"
            assert reset.json()["completed_at"] is None
            assert reset.json()["mode_intent"] is None
            assert reset.json()["setup_step"] is None
            assert reset.json()["model_profile_id"] is None
            assert (await client.get("/api/v1/onboarding")).json()["status"] == "not_started"
            assert sdk_marker.read_text(encoding="utf-8") == "keep-sdk-memory"

    asyncio.run(scenario())


def test_resetting_before_completion_is_not_an_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app), _client(app) as client:
            reset = await client.post("/api/v1/onboarding/reset")

            assert reset.status_code == 200
            assert reset.json()["status"] == "not_started"

    asyncio.run(scenario())


def test_sdk_unavailability_cannot_block_onboarding_completion(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app), _client(app) as client:

            async def unavailable() -> dict[str, object]:
                raise RuntimeError("hy_sdk_unavailable")

            app.state.sdk_memory_runtime.digest = unavailable
            finished = await client.post("/api/v1/onboarding/complete")

            assert finished.status_code == 200
            assert finished.json()["status"] == "completed"

    asyncio.run(scenario())


def test_progress_patch_and_put_round_trip_only_resumable_choices(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app), _client(app) as client:
            patched = await client.patch(
                "/api/v1/onboarding/progress",
                json={"mode_intent": "pro", "setup_step": "mode"},
            )
            assert patched.status_code == 200
            assert patched.json()["mode_intent"] == "pro"
            assert patched.json()["setup_step"] == "mode"
            assert patched.json()["model_profile_id"] is None

            profile_id = "9be6d465-4cf2-4c72-93f7-15f3c012bc80"
            replaced = await client.put(
                "/api/v1/onboarding/progress",
                json={"setup_step": "model", "model_profile_id": profile_id},
            )
            assert replaced.status_code == 200
            assert replaced.json()["mode_intent"] == "pro"
            assert replaced.json()["setup_step"] == "model"
            assert replaced.json()["model_profile_id"] == profile_id
            assert (await client.get("/api/v1/onboarding")).json() == replaced.json()

            ultra = await client.put(
                "/api/v1/onboarding/progress",
                json={"mode_intent": "ultra", "setup_step": "model"},
            )
            assert ultra.status_code == 200
            assert ultra.json()["mode_intent"] == "ultra"

    asyncio.run(scenario())


def test_progress_rejects_unknown_or_invalid_fields(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app), _client(app) as client:
            secret = await client.patch(
                "/api/v1/onboarding/progress",
                json={"mode_intent": "lite", "api_key": "must-not-be-stored"},
            )
            invalid_id = await client.put(
                "/api/v1/onboarding/progress",
                json={"model_profile_id": "not-a-profile-id"},
            )
            invalid_mode = await client.patch(
                "/api/v1/onboarding/progress",
                json={"mode_intent": "future"},
            )

            assert secret.status_code == 422
            assert "must-not-be-stored" not in secret.text
            assert invalid_id.status_code == 422
            assert invalid_mode.status_code == 422
            state = (await client.get("/api/v1/onboarding")).json()
            assert state["mode_intent"] is None
            assert state["model_profile_id"] is None

    asyncio.run(scenario())


def test_progress_row_adapter_accepts_mysql_tuple_shape() -> None:
    started_at = datetime(2026, 11, 4, 1, 0, tzinfo=UTC)
    updated_at = datetime(2026, 11, 4, 1, 1)
    state = OnboardingStore._to_state(
        (
            1,
            "importing",
            '["cursor"]',
            started_at,
            None,
            updated_at,
            "lite",
            "review",
            None,
        ),
        mysql=True,
    )

    assert state.selected_source_ids == ("cursor",)
    assert state.started_at == started_at
    assert state.mode_intent == "lite"
    assert state.setup_step == "review"


def test_progress_row_adapter_accepts_real_sqlite_row_and_text_datetime() -> None:
    with sqlite3.connect(":memory:") as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT 1 AS singleton, 'importing' AS status, '[\"cursor\"]' "
            "AS selected_source_ids_json, '2026-11-04T01:00:00+00:00' AS started_at, "
            "NULL AS completed_at, '2026-11-04T01:01:00+00:00' AS updated_at, "
            "'pro' AS mode_intent, 'model' AS setup_step, "
            "'9be6d465-4cf2-4c72-93f7-15f3c012bc80' AS model_profile_id"
        ).fetchone()
    assert row is not None

    state = OnboardingStore._to_state(row, mysql=False)

    assert state.started_at == datetime(2026, 11, 4, 1, 0, tzinfo=UTC)
    assert state.mode_intent == "pro"
    assert state.setup_step == "model"


def test_progress_row_adapter_rejects_wrong_length() -> None:
    with pytest.raises(ValueError, match="onboarding_row_shape_invalid"):
        OnboardingStore._to_state((1, "importing"), mysql=True)


def test_progress_row_adapter_rejects_wrong_row_types() -> None:
    values = (
        1,
        "importing",
        "[]",
        None,
        None,
        datetime(2026, 11, 4, 1, 1),
        None,
        None,
        None,
    )
    with pytest.raises(ValueError, match="onboarding_row_shape_invalid"):
        OnboardingStore._to_state(list(values), mysql=True)
    with pytest.raises(ValueError, match="onboarding_row_shape_invalid"):
        OnboardingStore._to_state(values, mysql=False)


def test_progress_row_adapter_rejects_wrong_engine_datetime_shape() -> None:
    with pytest.raises(ValueError, match="onboarding_datetime_invalid"):
        OnboardingStore._to_state(
            (
                1,
                "importing",
                "[]",
                "2026-11-04T01:00:00+00:00",
                None,
                datetime(2026, 11, 4, 1, 1),
                None,
                None,
                None,
            ),
            mysql=True,
        )


def test_runtime_sqlite_upgrade_does_not_record_progress_migration(tmp_path: Path) -> None:
    database = tmp_path / "old.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL
            );
            INSERT INTO schema_migrations VALUES (61, 'write_job_retries', '2026-11-03');
            CREATE TABLE onboarding_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                status TEXT NOT NULL,
                selected_source_ids_json TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )

    OnboardingStore(database)

    with sqlite3.connect(database) as connection:
        recorded = connection.execute(
            "SELECT name FROM schema_migrations WHERE version=62"
        ).fetchone()
        columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(onboarding_state)")}
    assert recorded is None
    assert {"mode_intent", "setup_step", "model_profile_id"} <= columns


def test_progress_rejects_updates_after_completion(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.router.lifespan_context(app), _client(app) as client:
            assert (await client.post("/api/v1/onboarding/complete")).status_code == 200

            populated = await client.patch(
                "/api/v1/onboarding/progress",
                json={"mode_intent": "lite"},
            )
            empty = await client.put("/api/v1/onboarding/progress", json={})

            assert populated.status_code == 409
            assert populated.json()["detail"] == "onboarding_completed"
            assert empty.status_code == 409
            state = (await client.get("/api/v1/onboarding")).json()
            assert state["status"] == "completed"
            assert state["mode_intent"] is None

    asyncio.run(scenario())
