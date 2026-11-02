"""A broken model configuration must not turn a backlog into a request storm (#245)."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from one_memory.api import create_app
from one_memory.config import Settings
from one_memory.memory_server.contracts import BatchIdentity, WriteRequest
from one_memory.memory_server.durable_queue import (
    FAILED,
    PENDING,
    DurableWriteDrain,
    DurableWriteQueue,
)
from one_memory.model_backoff import (
    BACKOFF_CAP_SECONDS,
    BackedOffModelTransport,
    ModelBackoff,
    ModelBackoffRefusal,
    backoff_seconds,
)
from one_memory.secrets import InMemorySecretStore

CHAT = "https://models.example.com/v1/chat/completions"
EMBEDDINGS = "https://models.example.com/v1/embeddings"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _request(index: int) -> WriteRequest:
    session = f"sess-{index}"
    return WriteRequest(
        internal_session_id=session,
        batch_identity=BatchIdentity(internal_session_id=session, first_round=1, last_round=5),
        evidence_ids=[f"e-{index}"],
        idempotency_key=f"idem-{index}",
        # Twenty sources, so the drain could claim ten jobs at once.
        source_id=f"source-{index % 20}",
    )


async def _never_execute(job_id: str) -> None:
    raise AssertionError(f"the simulation settles {job_id} itself")


def _send(transport: BackedOffModelTransport, url: str = CHAT) -> int | None:
    try:
        response = asyncio.run(transport.handle_async_request(httpx.Request("POST", url)))
    except ModelBackoffRefusal:
        return None
    return response.status_code


def test_the_pause_doubles_from_five_seconds_and_stops_at_three_minutes() -> None:
    assert [backoff_seconds(level) for level in range(1, 9)] == [
        5.0,
        10.0,
        20.0,
        40.0,
        80.0,
        160.0,
        180.0,
        180.0,
    ]
    assert backoff_seconds(1000) == BACKOFF_CAP_SECONDS == 180.0


def test_an_always_failing_provider_sees_a_bounded_number_of_calls(tmp_path) -> None:
    """200 pending jobs against a provider that refuses every one.

    Before the pause the drain claimed the next job the moment the last one
    failed: hundreds of requests a minute. Now the calls follow the schedule.
    """
    clock = _Clock()
    backoff = ModelBackoff(clock=clock)
    calls: list[float] = []

    def provider(_request: httpx.Request) -> httpx.Response:
        calls.append(clock.now)
        return httpx.Response(401)

    transport = BackedOffModelTransport(
        backoff, profile_id="p:1", inner=httpx.MockTransport(provider)
    )
    queue = DurableWriteQueue(tmp_path / "one-memory.sqlite3")
    for index in range(200):
        queue.submit(_request(index))
    drain = DurableWriteDrain(
        queue,
        execute=_never_execute,
        owner="sim",
        model_claim_allowance=backoff.allowance,
    )

    claimed = 0
    for tick in range(6001):
        # Ten minutes in tenths of a second, without float drift.
        clock.now = tick / 10
        for job_id in drain.claim(limit=10):
            claimed += 1
            _send(transport)
            queue.settle(job_id, state=FAILED, owner="sim", error_code="model_auth_failed")

    # The first claim was taken before anything had failed: two of its
    # requests reached the provider and the rest were refused locally. After
    # that one probe per pause: 5, 10, 20, 40, 80, 160, then 180 seconds.
    assert calls[:2] == [0.0, 0.0]
    probes = calls[2:]
    assert probes == pytest.approx([5.0, 15.0, 35.0, 75.0, 155.0, 315.0, 495.0], abs=0.11)
    gaps = [later - earlier for earlier, later in zip(probes, probes[1:], strict=False)]
    assert max(gaps) <= BACKOFF_CAP_SECONDS + 0.11
    assert len(calls) == 9
    assert claimed == 17
    with sqlite3.connect(tmp_path / "one-memory.sqlite3") as database:
        (pending,) = database.execute(
            "SELECT COUNT(*) FROM memory_write_jobs WHERE state=?", (PENDING,)
        ).fetchone()
    assert pending == 200 - claimed


def test_a_success_ends_the_pause_at_once() -> None:
    clock = _Clock()
    backoff = ModelBackoff(clock=clock)
    replies = [httpx.Response(503), httpx.Response(503), httpx.Response(200)]
    transport = BackedOffModelTransport(
        backoff, profile_id="p:1", inner=httpx.MockTransport(lambda _r: replies.pop(0))
    )

    assert _send(transport) == 503
    clock.now = 5.0
    assert backoff.allowance() == 1
    assert _send(transport) == 503
    assert backoff.allowance() == 0
    assert backoff.state().last_error == "model_server_error"

    clock.now = 15.0
    assert backoff.allowance() == 1
    assert _send(transport) == 200
    assert backoff.allowance() is None
    assert backoff.state().until is None
    assert backoff.state().failures == 0


def test_a_working_embedding_endpoint_does_not_clear_a_broken_chat_model() -> None:
    clock = _Clock()
    backoff = ModelBackoff(clock=clock)

    def provider(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200 if request.url.path.endswith("/embeddings") else 404)

    transport = BackedOffModelTransport(
        backoff, profile_id="p:1", inner=httpx.MockTransport(provider)
    )
    assert _send(transport) == 404
    assert _send(transport) == 404
    # Recall's embeddings still go out while the chat model is refused.
    assert _send(transport, EMBEDDINGS) == 200
    assert _send(transport) is None
    assert backoff.state().last_error == "model_not_found"
    assert backoff.paused()


def test_an_unreachable_provider_is_paused_like_a_refusing_one() -> None:
    clock = _Clock()
    backoff = ModelBackoff(clock=clock)

    def provider(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("name resolution failed", request=request)

    transport = BackedOffModelTransport(
        backoff, profile_id="p:1", inner=httpx.MockTransport(provider)
    )
    with pytest.raises(httpx.ConnectError):
        _send(transport)
    assert backoff.state().last_error == "model_unreachable"
    assert backoff.allowance() == 0


def test_a_request_problem_is_left_to_its_job() -> None:
    backoff = ModelBackoff(clock=_Clock())
    transport = BackedOffModelTransport(
        backoff, profile_id="p:1", inner=httpx.MockTransport(lambda _r: httpx.Response(400))
    )
    assert _send(transport) == 400
    assert backoff.allowance() is None


def test_a_configuration_change_ends_the_pause() -> None:
    clock = _Clock()
    backoff = ModelBackoff(clock=clock)
    for _ in range(6):
        clock.now += 200.0
        backoff.record_failure("model_auth_failed", "p:1", "chat")
    assert backoff.state().until is not None

    backoff.reset()

    assert backoff.allowance() is None
    assert backoff.state().until is None
    # A new revision of the profile is a new configuration as well.
    backoff.record_failure("model_auth_failed", "p:1", "chat")
    backoff.record_failure("model_auth_failed", "p:2", "chat")
    assert backoff.state().failures == 1


def test_a_probe_that_never_reports_does_not_hold_the_queue_forever() -> None:
    clock = _Clock()
    backoff = ModelBackoff(clock=clock)
    backoff.record_failure("model_timeout", "p:1", "chat")
    clock.now = 5.0
    assert backoff.allowance() == 1
    assert backoff.allowance() == 0
    clock.now = 35.0
    assert backoff.allowance() == 1


def test_memory_processing_shows_the_pause_and_a_profile_save_ends_it(tmp_path) -> None:
    async def scenario() -> None:
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path,
                api_token=SecretStr("local-token"),
            ),
            secret_store=InMemorySecretStore(),
        )
        headers = {"Authorization": "Bearer local-token", "Origin": "http://127.0.0.1:5173"}
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test", headers=headers
            ) as client,
        ):
            created = await client.post(
                "/api/v1/model-profiles",
                json={
                    "name": "Provider",
                    "provider_kind": "openai_compatible",
                    "base_url": "https://models.example.com/v1",
                    "model": "memory-model",
                    "embedding_model": "embedding-model",
                    "embedding_base_url": "https://models.example.com/v1",
                    "api_key": "api-route-secret",
                },
            )
            assert created.status_code == 200
            backoff: ModelBackoff = app.state.model_backoff
            backoff.record_failure("model_auth_failed", "p:1", "chat")

            paused = (await client.get("/api/v1/memory-processing")).json()
            assert paused["llm_backoff_error"] == "model_auth_failed"
            until = datetime.fromisoformat(paused["llm_backoff_until"])
            assert until > datetime.now(UTC)
            assert paused["suspended"] == 0

            saved = await client.patch(
                f"/api/v1/model-profiles/{created.json()['profile_id']}",
                json={"expected_revision": created.json()["revision"], "model": "other-model"},
            )
            assert saved.status_code == 200

            cleared = (await client.get("/api/v1/memory-processing")).json()
            assert cleared["llm_backoff_until"] is None
            assert cleared["llm_backoff_error"] is None
            assert backoff.allowance() is None

    asyncio.run(scenario())
