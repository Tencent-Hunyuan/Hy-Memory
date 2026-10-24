"""The sedimentation API persists decisions and resumes the SDK queue."""

import asyncio
import threading

from fastapi.testclient import TestClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings


def _app(tmp_path):
    return create_app(
        Settings(
            _env_file=None,
            data_dir=tmp_path / "data",
            source_home=tmp_path / "home",
            api_token=SecretStr("test"),
            imports_enabled=False,
            automatic_memory_processing=False,
            live_sync_enabled=False,
        ),
        source_catalog={},
    )


def test_sedimentation_route_persists_and_resumes_without_restart(tmp_path) -> None:
    with TestClient(_app(tmp_path), headers={"Authorization": "Bearer test"}) as client:
        assert client.get("/api/v1/sedimentation").json()["enabled"] is True
        assert client.put(
            "/api/v1/sedimentation", json={"enabled": False}
        ).json()["enabled"] is False
        assert client.get("/api/v1/sedimentation").json()["enabled"] is False
        assert client.put(
            "/api/v1/sedimentation", json={"enabled": True}
        ).json()["enabled"] is True


def test_switch_answers_before_sdk_queue_resume_finishes(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path)
    released = threading.Event()
    finished = threading.Event()

    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
        async def slow_resume() -> None:
            await asyncio.get_running_loop().run_in_executor(None, released.wait, 10.0)
            finished.set()

        monkeypatch.setattr(app.state.sdk_memory_runtime, "resume_allowed", slow_resume)
        assert client.put(
            "/api/v1/sedimentation", json={"enabled": False}
        ).status_code == 200
        resumed = client.put("/api/v1/sedimentation", json={"enabled": True})

        assert resumed.status_code == 200
        assert resumed.json()["enabled"] is True
        assert finished.is_set() is False
        released.set()


def test_a_failing_sdk_queue_resume_does_not_fail_the_switch(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path)
    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
        async def broken_resume() -> None:
            raise RuntimeError("sdk_queue_unavailable")

        monkeypatch.setattr(app.state.sdk_memory_runtime, "resume_allowed", broken_resume)
        assert client.put(
            "/api/v1/sedimentation", json={"enabled": False}
        ).status_code == 200
        resumed = client.put("/api/v1/sedimentation", json={"enabled": True})

        assert resumed.status_code == 200
        assert resumed.json()["enabled"] is True
