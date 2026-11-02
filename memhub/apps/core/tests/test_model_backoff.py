"""A broken model configuration must not turn a backlog into a request storm (#245)."""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

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
    execute_job,
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


# --- Every write retries itself: provider failures wait, others retry 3 times ---


class _FakeSdk:
    """Stands in for the SDK write path, with its real replay rule.

    Like `HyMemoryClient`, it keeps one outcome per request id and replays a
    failed one without calling the model. Every write goes out through the
    breaker's transport, so the provider sees exactly what MemHub sends.
    """

    def __init__(self, transport: BackedOffModelTransport | None = None) -> None:
        self.transport = transport
        self.outcomes: dict[str, str] = {}
        self.written: list[str] = []
        self.request_ids: list[str] = []
        self.fail_with: str | None = None

    async def process_sdk_job(self, records, *, request_id, before_hy_invocation):
        await before_hy_invocation()
        self.request_ids.append(request_id)
        if request_id in self.outcomes:
            if self.outcomes[request_id] == "failed":
                raise RuntimeError("hy_sdk_write_failed")
            raise AssertionError("a succeeded id is never sent again")
        if self.fail_with is not None:
            self.outcomes[request_id] = "failed"
            raise RuntimeError(self.fail_with)
        if self.transport is not None:
            try:
                response = await self.transport.handle_async_request(httpx.Request("POST", CHAT))
            except httpx.TransportError:
                self.outcomes[request_id] = "unknown"
                raise
            if response.status_code >= 400:
                self.outcomes[request_id] = "failed"
                raise RuntimeError("hy_sdk_write_failed")
        self.outcomes[request_id] = "succeeded"
        self.written.extend(record.evidence_id for record in records)
        return SimpleNamespace(raw_ids=(f"raw-{request_id}",), memory_ids=(f"m-{request_id}",))

    async def inquire_write_outcome(self, request_id):
        if self.outcomes.get(request_id) != "succeeded":
            return None
        return SimpleNamespace(raw_ids=(f"raw-{request_id}",), memory_ids=(f"m-{request_id}",))


class _Evidence:
    def get(self, evidence_id: str):
        return SimpleNamespace(evidence_id=evidence_id, source_id="cursor", user_id="local")


def _ordered_request(index: int, *, source: str = "cursor") -> WriteRequest:
    return WriteRequest(
        internal_session_id="sess-1",
        batch_identity=BatchIdentity(
            internal_session_id="sess-1", first_round=index + 1, last_round=index + 1
        ),
        evidence_ids=[f"e-{index:03d}"],
        idempotency_key=f"idem-{index:03d}",
        source_id=source,
        event_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=index),
    )


def _jobs(path) -> list[tuple[str, str, int, int, str | None]]:
    with sqlite3.connect(path) as database:
        return database.execute(
            "SELECT idempotency_key,state,sdk_attempt,retries,last_error "
            "FROM memory_write_jobs ORDER BY queue_seq"
        ).fetchall()


def test_an_outage_with_200_queued_jobs_ends_with_every_job_written_once_in_order(
    tmp_path,
) -> None:
    """The provider refuses every request for five minutes, then recovers.

    No job is lost, skipped, written twice or left `unknown`; the order is
    the order of the conversation; the provider sees a handful of probes
    during the outage, not one request per job.
    """
    clock = _Clock()
    backoff = ModelBackoff(clock=clock)
    calls: list[tuple[float, int]] = []

    def provider(_request: httpx.Request) -> httpx.Response:
        status = 401 if clock.now < 300.0 else 200
        calls.append((clock.now, status))
        return httpx.Response(status)

    sdk = _FakeSdk(
        BackedOffModelTransport(backoff, profile_id="p:1", inner=httpx.MockTransport(provider))
    )
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    for index in range(200):
        queue.submit(_ordered_request(index))
    drain = DurableWriteDrain(
        queue,
        execute=_never_execute,
        owner="outage",
        model_claim_allowance=backoff.allowance,
    )

    async def tick() -> None:
        await asyncio.gather(
            *(
                execute_job(
                    job_id,
                    queue=queue,
                    supervisor=sdk,
                    evidence=_Evidence(),
                    owner="outage",
                    model_failures=backoff.attempt,
                )
                for job_id in drain.claim(limit=10)
            )
        )

    for second in range(800):
        clock.now = float(second)
        asyncio.run(tick())

    jobs = _jobs(path)
    assert [state for _, state, *_ in jobs] == ["completed"] * 200
    assert sdk.written == [f"e-{index:03d}" for index in range(200)]
    outage = [at for at, status in calls if status == 401]
    # One request at 0 s, then one probe per pause: 5, 15, 35, 75, 155 s.
    assert outage == [0.0, 5.0, 15.0, 35.0, 75.0, 155.0]
    # The first job was tried seven times; each try had its own SDK id, so
    # the recovered provider was really called, not a replayed failure.
    assert jobs[0][2:4] == (6, 0)
    assert sdk.request_ids[:3] == ["idem-000", "idem-000#r1", "idem-000#r2"]
    assert all(retries == 0 for *_, retries, _ in jobs)


def test_a_failure_of_the_job_itself_is_retried_three_times_then_skipped(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    first = queue.submit(_ordered_request(0))
    second = queue.submit(_ordered_request(1))
    sdk = _FakeSdk()
    drain = DurableWriteDrain(queue, execute=_never_execute, owner="skip")
    now = datetime.now(UTC)

    def run(at: datetime) -> tuple[str, ...]:
        claimed = drain.claim(limit=1, now=at)
        for job_id in claimed:
            asyncio.run(
                execute_job(
                    job_id,
                    queue=queue,
                    supervisor=sdk,
                    evidence=_Evidence(),
                    owner="skip",
                    model_failures=ModelBackoff().attempt,
                )
            )
        return claimed

    sdk.fail_with = "model_content_json_parse_failed"
    assert run(now) == (first.job_id,)
    # Each retry waits longer: 30 s, 60 s, 120 s. Until then the claim skips
    # the job, and the job after it in the conversation waits behind it.
    assert run(now + timedelta(seconds=20)) == ()
    assert run(now + timedelta(seconds=40)) == (first.job_id,)
    assert run(now + timedelta(seconds=150)) == (first.job_id,)
    assert run(now + timedelta(seconds=400)) == (first.job_id,)
    assert _jobs(path)[0][1:] == ("failed", 3, 3, "model_content_json_parse_failed")
    assert queue.get("idem-000").error_code == "model_content_json_parse_failed"

    sdk.fail_with = None
    assert run(now + timedelta(seconds=401)) == (second.job_id,)
    assert _jobs(path)[1][1] == "completed"
    assert sdk.request_ids == [
        "idem-000",
        "idem-000#r1",
        "idem-000#r2",
        "idem-000#r3",
        "idem-001",
    ]


def test_a_write_interrupted_mid_call_is_rerun_under_a_new_request_id(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_ordered_request(0))
    drain = DurableWriteDrain(queue, execute=_never_execute, owner="crashed")
    assert drain.claim(limit=1) == (record.job_id,)
    assert queue.mark_invoked(record.job_id, owner="crashed")
    # The process stops; on restart the lease has lapsed.
    with sqlite3.connect(path) as database:
        database.execute(
            "UPDATE memory_write_jobs SET lease_expires_at=? WHERE job_id=?",
            ("2000-01-01T00:00:00+00:00", record.job_id),
        )
    assert queue.reclaim_expired() == 1
    assert queue.get("idem-000").state == "unknown"
    # Still running on its thread in this process: left alone.
    assert queue.recover_unknown(in_flight=lambda request_id: request_id == "idem-000") == 0

    assert queue.recover_unknown() == 1
    assert _jobs(path)[0][1:] == ("pending", 1, 1, "durable_write_interrupted")
    assert queue.recover_unknown() == 0

    sdk = _FakeSdk()
    later = datetime.now(UTC) + timedelta(seconds=31)
    rerun = DurableWriteDrain(queue, execute=_never_execute, owner="restarted")
    assert rerun.claim(limit=1, now=later) == (record.job_id,)
    asyncio.run(
        execute_job(
            record.job_id, queue=queue, supervisor=sdk, evidence=_Evidence(), owner="restarted"
        )
    )
    assert sdk.request_ids == ["idem-000#r1"]
    assert queue.get("idem-000").state == "completed"


def test_a_write_that_did_land_is_completed_from_its_receipt_not_rerun(tmp_path) -> None:
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    record = queue.submit(_ordered_request(0))
    with sqlite3.connect(path) as database:
        database.execute(
            "UPDATE memory_write_jobs SET state='unknown',hy_invoked=1,"
            "last_error='durable_work_ceiling_exceeded' WHERE job_id=?",
            (record.job_id,),
        )
    sdk = _FakeSdk()
    sdk.outcomes["idem-000"] = "succeeded"
    assert queue.recover_unknown() == 1
    drain = DurableWriteDrain(queue, execute=_never_execute, owner="landed")
    assert drain.claim(limit=1, now=datetime.now(UTC) + timedelta(seconds=31)) == (record.job_id,)
    asyncio.run(
        execute_job(
            record.job_id, queue=queue, supervisor=sdk, evidence=_Evidence(), owner="landed"
        )
    )
    assert sdk.request_ids == []
    completed = queue.get("idem-000")
    assert completed.state == "completed"
    assert completed.sdk_memory_ids == ("m-idem-000",)


def test_unknown_jobs_from_before_the_upgrade_are_handled_once(tmp_path) -> None:
    from one_memory.memory_tasks import NON_RETRYABLE_POST_WRITE_CODES

    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    plain = queue.submit(_ordered_request(0, source="a"))
    verified = queue.submit(_ordered_request(1, source="b"))
    looping = queue.submit(_ordered_request(2, source="c"))
    with sqlite3.connect(path) as database:
        for job, code, retries in (
            (plain, "hy_sdk_write_failed", 0),
            (verified, NON_RETRYABLE_POST_WRITE_CODES[0], 0),
            (looping, "durable_write_interrupted", 3),
        ):
            database.execute(
                "UPDATE memory_write_jobs SET state='unknown',hy_invoked=1,attempts=1,"
                "last_error=?,retries=? WHERE job_id=?",
                (code, retries, job.job_id),
            )

    assert queue.recover_unknown() == 3
    assert queue.recover_unknown() == 0
    # Reopening the queue (a restart) changes nothing either.
    assert DurableWriteQueue(path).recover_unknown() == 0
    states = {key: (state, attempt) for key, state, attempt, _, _ in _jobs(path)}
    assert states == {
        "idem-000": ("pending", 1),
        # The write landed against an authority Core could not verify.
        "idem-001": ("failed", 0),
        # A crash loop ends instead of spinning.
        "idem-002": ("failed", 0),
    }


def test_concurrent_writes_are_all_charged_with_a_provider_failure() -> None:
    """The breaker cannot tell whose request failed, so every write in flight waits.

    A write the provider did fail must never spend its retries on the outage;
    a write that failed beside it for its own reason waits for the provider
    once, and is retried with the rest.
    """
    backoff = ModelBackoff(clock=_Clock())
    with backoff.attempt() as first, backoff.attempt() as second:
        backoff.record_failure("model_server_error", "p:1", "chat")
    with backoff.attempt() as later:
        pass
    assert (first.failure, second.failure, later.failure) == (
        "model_server_error",
        "model_server_error",
        None,
    )


def test_the_manual_retry_endpoint_is_gone(tmp_path) -> None:
    async def scenario() -> int:
        app = create_app(
            Settings(
                data_dir=tmp_path / "data",
                source_home=tmp_path,
                api_token=SecretStr("local-token"),
            ),
            secret_store=InMemorySecretStore(),
        )
        async with (
            app.router.lifespan_context(app),
            AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer local-token"},
            ) as client,
        ):
            response = await client.post(
                "/api/v1/memory-processing/retry-unknown", json={"confirm": True}
            )
            return response.status_code

    assert asyncio.run(scenario()) in {404, 405}
