"""Onboarding endpoints manage UI state without touching SDK memory."""

import asyncio
from pathlib import Path

from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings


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

            assert (await client.post("/api/v1/onboarding/complete")).status_code == 200
            reset = await client.post("/api/v1/onboarding/reset")

            assert reset.status_code == 200
            assert reset.json()["status"] == "not_started"
            assert reset.json()["completed_at"] is None
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
