"""The sedimentation API persists decisions without rewriting parked work."""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.memory_server.contracts import BatchIdentity, WriteRequest
from one_memory.memory_server.durable_queue import WAITING_FOR_PERMISSION


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


def test_sedimentation_route_persists_without_restart(tmp_path) -> None:
    with TestClient(_app(tmp_path), headers={"Authorization": "Bearer test"}) as client:
        assert client.get("/api/v1/sedimentation").json()["enabled"] is True
        assert (
            client.put("/api/v1/sedimentation", json={"enabled": False}).json()["enabled"] is False
        )
        assert client.get("/api/v1/sedimentation").json()["enabled"] is False
        assert client.put("/api/v1/sedimentation", json={"enabled": True}).json()["enabled"] is True


def test_switch_does_not_run_the_broad_parked_queue_resume(tmp_path, monkeypatch) -> None:
    app = _app(tmp_path)
    called = False

    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:

        async def forbidden_resume() -> None:
            nonlocal called
            called = True

        monkeypatch.setattr(app.state.sdk_memory_runtime, "resume_allowed", forbidden_resume)
        assert client.put("/api/v1/sedimentation", json={"enabled": False}).status_code == 200
        resumed = client.put("/api/v1/sedimentation", json={"enabled": True})

        assert resumed.status_code == 200
        assert resumed.json()["enabled"] is True
        assert called is False


def test_switch_does_not_rewrite_permission_parked_jobs(tmp_path) -> None:
    app = _app(tmp_path)
    with TestClient(app, headers={"Authorization": "Bearer test"}) as client:
        queue = app.state.memory_write_queue
        queue.evidence = None
        request = WriteRequest(
            internal_session_id="session-1",
            source_id="cursor",
            batch_identity=BatchIdentity(
                internal_session_id="session-1",
                first_round=1,
                last_round=1,
            ),
            evidence_ids=["e-1"],
            idempotency_key="permission-parked",
        )
        record = queue.submit(request)
        queue.settle(
            record.job_id,
            state=WAITING_FOR_PERMISSION,
            error_code="model_content_permission_required",
            hy_invoked=False,
        )

        assert client.put("/api/v1/sedimentation", json={"enabled": False}).status_code == 200
        assert client.put("/api/v1/sedimentation", json={"enabled": True}).status_code == 200

        parked = queue.get("permission-parked")
        assert parked.state == WAITING_FOR_PERMISSION
        assert parked.detail == "model_content_permission_required"
