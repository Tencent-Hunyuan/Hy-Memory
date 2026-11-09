"""Cancelling a queued write before it becomes a memory (issue #381)."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from one_memory.memory_server.durable_queue import DurableWriteDrain, DurableWriteRegistry
from one_memory.memory_server.routes import sdk_router

ROUTE = "/api/v2/memory-writes"


def _payload(**overrides: object) -> dict:
    body: dict[str, object] = {
        "request_id": "request-1",
        "session_id": "session-1",
        "messages": [{"role": "user", "content": "remember the deploy window"}],
    }
    body.update(overrides)
    return body


def _client(path) -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    app.state.memory_write_queue_path = path
    app.state.memory_authority = "sdk"
    app.include_router(sdk_router())
    return app, TestClient(app)


def _row(path, request_id: str) -> tuple[str, str | None]:
    with sqlite3.connect(path) as db:
        return db.execute(
            "SELECT state, lease_owner FROM memory_write_jobs WHERE idempotency_key=?",
            (request_id,),
        ).fetchone()


def test_cancel_a_pending_write_and_read_it_back(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    _app, client = _client(path)
    request_id = client.post(ROUTE, json=_payload()).json()["request_id"]

    response = client.delete(f"{ROUTE}/{request_id}")

    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
    assert response.json()["request_id"] == request_id
    assert _row(path, request_id)[0] == "cancelled"
    assert client.get(f"{ROUTE}/{request_id}").json()["state"] == "cancelled"


def test_cancelling_twice_is_conflict_not_a_second_outcome(tmp_path) -> None:
    _app, client = _client(tmp_path / "one-memory.sqlite3")
    request_id = client.post(ROUTE, json=_payload()).json()["request_id"]

    assert client.delete(f"{ROUTE}/{request_id}").status_code == 200
    second = client.delete(f"{ROUTE}/{request_id}")

    assert second.status_code == 409
    assert second.json()["detail"] == "write_not_cancellable"


def test_cancel_an_unknown_id_is_404(tmp_path) -> None:
    _app, client = _client(tmp_path / "one-memory.sqlite3")
    client.post(ROUTE, json=_payload())

    assert client.delete(f"{ROUTE}/absent").status_code == 404


def test_cancel_a_completed_write_is_refused(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    _app, client = _client(path)
    request_id = client.post(ROUTE, json=_payload()).json()["request_id"]
    registry = DurableWriteRegistry(path, sdk_authority=True)
    registry.queue.settle(registry.queue.get(request_id).job_id, state="completed")

    response = client.delete(f"{ROUTE}/{request_id}")

    assert response.status_code == 409
    assert _row(path, request_id)[0] == "completed"


def test_cancel_a_claimed_write_is_refused(tmp_path) -> None:
    async def never_execute(_job_id: str) -> None:
        return None

    path = tmp_path / "one-memory.sqlite3"
    app, client = _client(path)
    request_id = client.post(ROUTE, json=_payload()).json()["request_id"]
    DurableWriteDrain(
        app.state.memory_write_registry.queue,
        execute=never_execute,
        owner="owner-a",
        lease=timedelta(seconds=30),
    ).claim(limit=1)

    response = client.delete(f"{ROUTE}/{request_id}")

    assert response.status_code == 409
    state, owner = _row(path, request_id)
    assert state == "running"
    assert owner == "owner-a"


def test_cancel_respects_the_user_namespace(tmp_path) -> None:
    _app, client = _client(tmp_path / "one-memory.sqlite3")
    submitted = client.post(ROUTE, json=_payload(user_id="alice")).json()
    request_id = submitted["request_id"]

    assert client.delete(f"{ROUTE}/{request_id}").status_code == 404
    assert client.delete(f"{ROUTE}/{request_id}?user_id=alice").status_code == 200
