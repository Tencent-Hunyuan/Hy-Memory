"""A3-01: the HTTP write entry, tested against a real FastAPI app.

Mounted on a minimal app rather than the production one, so the test proves the
router's own contract (status codes, replay, conflict) instead of depending on
the whole server's auth and state wiring. Core serves only the SDK write door,
`/api/v2/memory-writes`.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from one_memory.memory_server.contracts import BatchIdentity, WriteRequest
from one_memory.memory_server.durable_queue import DurableWriteDrain, DurableWriteRegistry
from one_memory.memory_server.routes import sdk_router

ROUTE = "/api/v2/memory-writes"


def _payload(**overrides: object) -> dict:
    body: dict[str, object] = {
        "request_id": "request-1",
        "session_id": "session-1",
        "messages": [
            {"role": "user", "content": "remember the deploy window"},
            {"role": "assistant", "content": "noted"},
        ],
    }
    body.update(overrides)
    return body


def _durable_client(path) -> tuple[FastAPI, TestClient]:
    app = FastAPI()
    app.state.memory_write_queue_path = path
    app.state.memory_authority = "sdk"
    app.include_router(sdk_router())
    return app, TestClient(app)


def test_submit_returns_202_and_replay_is_not_a_second_write(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    app, client = _durable_client(path)

    first = client.post(ROUTE, json=_payload())
    second = client.post(ROUTE, json=_payload())

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["request_id"] == second.json()["request_id"]
    assert "already accepted" in second.json()["detail"]
    assert isinstance(app.state.memory_write_registry, DurableWriteRegistry)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM memory_write_jobs").fetchone()[0] == 1


def test_a_write_without_a_user_turn_is_rejected(tmp_path) -> None:
    _app, client = _durable_client(tmp_path / "one-memory.sqlite3")

    response = client.post(
        ROUTE,
        json=_payload(messages=[{"role": "assistant", "content": "only me"}]),
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "messages_require_user_turn"


def test_unknown_fields_are_rejected(tmp_path) -> None:
    _app, client = _durable_client(tmp_path / "one-memory.sqlite3")

    assert client.post(ROUTE, json=_payload(evidence_ids=["e1"])).status_code == 422


def test_status_of_an_unknown_id_is_404(tmp_path) -> None:
    _app, client = _durable_client(tmp_path / "one-memory.sqlite3")
    client.post(ROUTE, json=_payload())

    assert client.get(f"{ROUTE}/absent").status_code == 404


def test_durable_route_maps_a_dead_letter_to_a_valid_failed_state(tmp_path) -> None:
    """`dead` is a queue concept; the HTTP contract has no such state."""
    database_path = tmp_path / "one-memory.sqlite3"
    _app, client = _durable_client(database_path)
    request_id = client.post(ROUTE, json=_payload()).json()["request_id"]

    registry = DurableWriteRegistry(database_path, sdk_authority=True)
    registry.queue.settle(
        registry.queue.get(request_id).job_id,
        state="dead",
        error_code="attempts exhausted",
    )

    response = client.get(f"{ROUTE}/{request_id}")
    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert "dead letter" in response.json()["detail"].lower()


def test_http_get_reports_a_claimed_running_job_as_accepted(tmp_path) -> None:
    """A live drain turns a submitted row `running`; GET must not 500."""

    async def never_execute(_job_id: str) -> None:
        return None

    path = tmp_path / "one-memory.sqlite3"
    app, client = _durable_client(path)
    request_id = client.post(ROUTE, json=_payload()).json()["request_id"]
    DurableWriteDrain(
        app.state.memory_write_registry.queue,
        execute=never_execute,
        owner="owner-a",
        lease=timedelta(seconds=30),
    ).claim(limit=1)

    response = client.get(f"{ROUTE}/{request_id}")

    assert response.status_code == 200
    assert response.json()["state"] == "accepted"


def test_read_get_does_not_reclaim_an_expired_lease(tmp_path) -> None:
    """A status GET must never turn an expired lease back into work.

    `DurableWriteQueue.__init__` reclaims expired leases. If the read handler
    builds the default registry, a GET rewrites a crashed worker's `running`
    row to `unknown`/`pending` — a read that mutates the queue and can collide
    with the drain. The read path must open without initializing.
    """
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteRegistry(path).queue
    created = queue.submit(
        WriteRequest(
            internal_session_id="sess-1",
            batch_identity=BatchIdentity(internal_session_id="sess-1", first_round=1, last_round=5),
            evidence_ids=["e1"],
            idempotency_key="idem-1",
        )
    )
    queue.settle(created.job_id, state="running")
    queue.mark_invoked(created.job_id)
    with sqlite3.connect(path) as db, db:
        db.execute(
            "UPDATE memory_write_jobs SET lease_owner='dead-worker', lease_expires_at=? "
            "WHERE job_id=?",
            ((datetime.now(UTC) - timedelta(hours=1)).isoformat(), created.job_id),
        )

    _app, client = _durable_client(path)

    def row():
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            return dict(
                db.execute(
                    "SELECT state, hy_invoked, lease_owner FROM memory_write_jobs WHERE job_id=?",
                    (created.job_id,),
                ).fetchone()
            )

    before = row()
    response = client.get(f"{ROUTE}/{created.request_id}")
    after = row()

    assert response.status_code == 200
    assert after == before, "a status GET rewrote an expired running lease"
    assert after["state"] == "running"
    assert after["hy_invoked"] == 1


def test_read_get_on_an_unmigrated_database_is_503_not_500(tmp_path) -> None:
    """A database file without the queue table is `unavailable`, not a crash."""
    path = tmp_path / "one-memory.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE unrelated (id INTEGER)")

    _app, client = _durable_client(path)

    assert client.get(f"{ROUTE}/does-not-exist").status_code == 503
