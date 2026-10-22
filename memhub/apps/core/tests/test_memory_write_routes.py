"""A3-01: the HTTP write entry, tested against a real FastAPI app.

Mounted on a minimal app rather than the production one, so the test proves the
router's own contract (status codes, replay, conflict) instead of depending on
the whole server's auth and state wiring.
"""

from __future__ import annotations

import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from one_memory.memory_server.durable_queue import DurableWriteRegistry
from one_memory.memory_server.registry import WriteRegistry
from one_memory.memory_server.routes import router


@pytest.fixture()
def client() -> TestClient:
    app = FastAPI()
    app.state.memory_write_registry = WriteRegistry()
    app.include_router(router())
    return TestClient(app)


def _payload(**overrides: object) -> dict:
    body = {
        "internal_session_id": "sess-1",
        "first_round": 1,
        "last_round": 5,
        "evidence_ids": ["e1"],
    }
    body.update(overrides)
    return body


def test_submit_returns_202_with_a_request_id(client: TestClient) -> None:
    response = client.post("/api/v1/memory-writes", json=_payload())
    assert response.status_code == 202
    assert response.json()["request_id"]
    assert response.json()["state"] == "accepted"


def test_a_replay_is_not_a_second_write(client: TestClient) -> None:
    """Duplicating a memory is worse than admitting it already exists."""
    first = client.post("/api/v1/memory-writes", json=_payload())
    second = client.post("/api/v1/memory-writes", json=_payload())
    assert second.status_code == 202
    assert second.json()["request_id"] == first.json()["request_id"]
    assert "already accepted" in second.json()["detail"]


def test_same_key_different_payload_is_409(client: TestClient) -> None:
    """Overwriting would discard the earlier intent."""
    client.post(
        "/api/v1/memory-writes",
        json=_payload(idempotency_key="fixed", evidence_ids=["e1"]),
    )
    conflict = client.post(
        "/api/v1/memory-writes",
        json=_payload(idempotency_key="fixed", evidence_ids=["different"]),
    )
    assert conflict.status_code == 409


def test_status_can_be_queried_after_submit(client: TestClient) -> None:
    created = client.post("/api/v1/memory-writes", json=_payload())
    request_id = created.json()["request_id"]
    status = client.get(f"/api/v1/memory-writes/{request_id}")
    assert status.status_code == 200
    assert status.json()["request_id"] == request_id


def test_status_of_an_unknown_id_is_404(client: TestClient) -> None:
    """'We never accepted this' must not read as 'accepted and failed'."""
    assert client.get("/api/v1/memory-writes/absent").status_code == 404


def test_missing_evidence_is_rejected(client: TestClient) -> None:
    response = client.post("/api/v1/memory-writes", json=_payload(evidence_ids=[]))
    assert response.status_code == 422


def test_unknown_fields_are_rejected(client: TestClient) -> None:
    """extra=forbid: a typo'd field must not be silently ignored."""
    response = client.post("/api/v1/memory-writes", json=_payload(evidences=["e1"]))
    assert response.status_code == 422


def test_no_registry_means_unavailable_not_a_crash(client: TestClient) -> None:
    app = FastAPI()
    app.include_router(router())
    bare = TestClient(app)
    assert bare.post("/api/v1/memory-writes", json=_payload()).status_code == 503


def test_durable_queue_can_back_the_same_route_contract(tmp_path) -> None:
    """The route shape must survive a process restart when backed by SQLite.

    This is the missing D52 bridge: the HTTP contract already exists, and the
    durable queue already records acceptance. The adapter must make the same
    POST/GET contract durable without changing the route or inventing a second
    idempotency rule.
    """
    path = tmp_path / "one-memory.sqlite3"
    app = FastAPI()
    app.state.memory_write_registry = DurableWriteRegistry(path)
    app.include_router(router())
    client = TestClient(app)

    created = client.post("/api/v1/memory-writes", json=_payload())
    assert created.status_code == 202
    request_id = created.json()["request_id"]
    assert created.json()["state"] == "accepted"

    reopened = FastAPI()
    reopened.state.memory_write_registry = DurableWriteRegistry(path)
    reopened.include_router(router())
    status = TestClient(reopened).get(f"/api/v1/memory-writes/{request_id}")
    assert status.status_code == 200
    assert status.json()["request_id"] == request_id
    assert status.json()["state"] == "accepted"

    replay = TestClient(reopened).post("/api/v1/memory-writes", json=_payload())
    assert replay.status_code == 202
    assert replay.json()["request_id"] == request_id
    assert "already accepted" in replay.json()["detail"]


def test_completed_write_status_returns_sdk_raw_and_memory_ids(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    app = FastAPI()
    registry = DurableWriteRegistry(path)
    app.state.memory_write_registry = registry
    app.include_router(router())
    client = TestClient(app)
    created = client.post("/api/v1/memory-writes", json=_payload())
    request_id = created.json()["request_id"]
    with sqlite3.connect(path) as database:
        job_id = str(
            database.execute(
                "SELECT job_id FROM memory_write_jobs WHERE idempotency_key=?",
                (request_id,),
            ).fetchone()[0]
        )
    registry.queue.settle(
        job_id,
        state="completed",
        sdk_raw_ids=("raw-1",),
        sdk_memory_ids=("memory-1",),
    )

    status = client.get(f"/api/v1/memory-writes/{request_id}")

    assert status.status_code == 200
    assert status.json()["raw_ids"] == ["raw-1"]
    assert status.json()["memory_ids"] == ["memory-1"]


def test_route_selects_durable_registry_when_a_path_is_configured(tmp_path) -> None:
    """C0 only has to hand the route a database path.

    The route keeps its legacy in-memory fallback for isolated tests, but when
    the production app provides `memory_write_queue_path`, the first request
    must create the durable adapter and write a real row.
    """
    path = tmp_path / "one-memory.sqlite3"
    app = FastAPI()
    app.state.memory_write_queue_path = path
    app.include_router(router())
    client = TestClient(app)

    created = client.post("/api/v1/memory-writes", json=_payload())

    assert created.status_code == 202
    assert isinstance(app.state.memory_write_registry, DurableWriteRegistry)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM memory_write_jobs").fetchone()[0] == 1


def test_durable_route_reports_unknown_without_collapsing_to_failed(tmp_path) -> None:
    """The HTTP state vocabulary must preserve the no-blind-retry outcome."""
    path = tmp_path / "one-memory.sqlite3"
    app = FastAPI()
    registry = DurableWriteRegistry(path)
    app.state.memory_write_registry = registry
    app.include_router(router())
    client = TestClient(app)

    created = client.post("/api/v1/memory-writes", json=_payload())
    request_id = created.json()["request_id"]
    assert registry.set_state(request_id, "unknown", "outcome not proven")
    response = client.get(f"/api/v1/memory-writes/{request_id}")

    assert response.status_code == 200
    assert response.json()["state"] == "unknown"


def test_durable_route_maps_a_dead_letter_to_a_valid_failed_state(tmp_path) -> None:
    """`dead` is a queue concept; the HTTP contract has no such state.

    Retries exhausted must still return a terminal receipt the response model
    accepts, without hiding that it was a dead letter rather than a normal
    failure.
    """
    app = FastAPI()
    database_path = tmp_path / "one-memory.sqlite3"
    app.state.memory_write_queue_path = database_path
    app.include_router(router())
    client = TestClient(app)
    created = client.post("/api/v1/memory-writes", json=_payload())
    request_id = created.json()["request_id"]

    registry = DurableWriteRegistry(database_path)
    registry.queue.settle(  # noqa: SLF001 - test needs the durable job id
        registry.queue.get(request_id).job_id,
        state="dead",
        error_code="attempts exhausted",
    )

    response = client.get(f"/api/v1/memory-writes/{request_id}")
    assert response.status_code == 200
    assert response.json()["state"] == "failed"
    assert "dead letter" in response.json()["detail"].lower()


def test_durable_configuration_overrides_an_in_memory_registry(tmp_path) -> None:
    """`api.py` always installs an in-memory registry; durable must still win.

    The production composition root sets `memory_write_registry` eagerly, so a
    durable router that preferred that object would silently accept writes
    into memory and lose them on restart. Setting the durable queue path must
    take precedence.
    """
    path = tmp_path / "one-memory.sqlite3"
    app = FastAPI()
    app.state.memory_write_registry = WriteRegistry()
    app.state.memory_write_queue_path = path
    app.include_router(router())
    client = TestClient(app)

    created = client.post("/api/v1/memory-writes", json=_payload())
    assert created.status_code == 202
    request_id = created.json()["request_id"]

    # The receipt is durable: it survives a brand-new registry built from the
    # same file, which the in-memory registry could not do.
    assert DurableWriteRegistry(path).get(request_id) is not None
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM memory_write_jobs").fetchone()[0] == 1


def test_route_passes_the_evidence_store_into_the_durable_registry(tmp_path, monkeypatch) -> None:
    """A configured Evidence store must enforce Evidence-first at the route.

    Without this wiring the queue accepts a write pointing at evidence the
    business database does not hold, and the failure only surfaces later as a
    `failed` drain job. When the composition root provides the store, the POST
    must be refused up front and nothing may be enqueued.
    """
    from one_memory.imports.storage import EvidenceStore

    path = tmp_path / "one-memory.sqlite3"
    app = FastAPI()
    app.state.memory_write_queue_path = path
    app.state.memory_write_evidence_store = EvidenceStore(path)
    app.include_router(router())
    client = TestClient(app)

    response = client.post(
        "/api/v1/memory-writes",
        json=_payload(evidence_ids=["does-not-exist"]),
    )

    assert response.status_code >= 400
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM memory_write_jobs").fetchone()[0] == 0


def test_http_get_reports_a_claimed_running_job_as_accepted(tmp_path) -> None:
    """A live drain turns a submitted row `running`; GET must not 500."""
    from datetime import timedelta

    from one_memory.memory_server.durable_queue import DurableWriteDrain

    async def never_execute(_job_id: str) -> None:
        return None

    path = tmp_path / "one-memory.sqlite3"
    app = FastAPI()
    registry = DurableWriteRegistry(path)
    app.state.memory_write_registry = registry
    app.include_router(router())
    client = TestClient(app)

    created = client.post("/api/v1/memory-writes", json=_payload())
    request_id = created.json()["request_id"]
    DurableWriteDrain(
        registry.queue,
        execute=never_execute,
        owner="owner-a",
        lease=timedelta(seconds=30),
    ).claim(limit=1)

    response = client.get(f"/api/v1/memory-writes/{request_id}")

    assert response.status_code == 200
    assert response.json()["state"] == "accepted"


def test_read_get_does_not_reclaim_an_expired_lease(tmp_path) -> None:
    """A status GET must never turn an expired lease back into work.

    `DurableWriteQueue.__init__` reclaims expired leases. If the read handler
    builds the default registry, a GET rewrites a crashed worker's `running`
    row to `unknown`/`pending` — a read that mutates the queue and can collide
    with the drain. The read path must open without initializing.
    """
    from datetime import UTC, datetime, timedelta

    from one_memory.memory_server.contracts import BatchIdentity, WriteRequest

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

    app = FastAPI()
    app.state.memory_write_queue_path = path
    app.include_router(router())
    client = TestClient(app)

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
    response = client.get(f"/api/v1/memory-writes/{created.request_id}")
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

    app = FastAPI()
    app.state.memory_write_queue_path = path
    app.include_router(router())
    client = TestClient(app)

    response = client.get("/api/v1/memory-writes/does-not-exist")

    assert response.status_code == 503
