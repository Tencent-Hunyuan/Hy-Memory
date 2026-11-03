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
from one_memory.imports.models import (
    EvidenceInput,
    EvidenceOrigin,
    OriginMetadata,
    WrittenBy,
    materialize_write_input,
)
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
from one_memory.sdk_write_processor import SdkWriteProcessor, sdk_write_failure_code
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


def test_claim_allowance_can_pace_embedding_without_pausing_lite_for_chat() -> None:
    backoff = ModelBackoff(clock=_Clock())
    backoff.record_failure("model_not_found", "p:1", "chat")
    assert backoff.allowance(kind="embedding") is None
    assert backoff.allowance(kind="chat") == 0

    backoff.reset()
    backoff.record_failure("embedding_service_failed", "p:1", "embedding")
    assert backoff.allowance(kind="embedding") == 0


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
        await before_hy_invocation(True)
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


# --- hy-memory 2.0.13: a model failure is `success: false`, never a silent success ---


class _Sdk2013Client:
    """`HyMemoryClient.add` as hy-memory 2.0.13 answers it.

    Each write makes one model call through MemHub's transport, so the
    breaker sees what the provider answered. The SDK turns a failed or
    unusable model reply into `success: false` with its stable codes; the
    shape of those results is the SDK's own (`_mark_reconcile_failure`,
    `CodingWriter`).
    """

    def __init__(self, transport: BackedOffModelTransport) -> None:
        self.transport = transport
        self.reply = "[]"
        self.messages: list[list[dict[str, str]]] = []

    def add(self, messages, **_kwargs):
        self.messages.append(messages)
        coding = any(message["role"] == "tool" for message in messages)
        try:
            response = asyncio.run(self.transport.handle_async_request(httpx.Request("POST", CHAT)))
        except httpx.TransportError:
            return self._failed(coding, "LLM_UNAVAILABLE", "LLM_ERROR")
        if response.status_code >= 400:
            return self._failed(coding, "LLM_UNAVAILABLE", "LLM_ERROR")
        if not self.reply:
            return self._failed(coding, "EMPTY_RESPONSE", "EMPTY_RESPONSE")
        if not self.reply.startswith("["):
            return self._failed(coding, "JSON_PARSE_FAILED", "JSON_PARSE_FAILED")
        return {"success": True, "memory_ids": ["m-1"]}

    @staticmethod
    def _failed(coding: bool, reconcile_cause: str, coding_code: str) -> dict[str, object]:
        if coding:
            return {"success": False, "agent_error_code": coding_code}
        return {
            "success": False,
            "error_code": 502,
            "agent_error_code": "RECONCILE_FAILED",
            "reconcile_error_code": reconcile_cause,
        }


class _Sdk2013Handle:
    mode = "pro"

    def __init__(self, client: _Sdk2013Client) -> None:
        self.sdk = client

    async def client(self) -> _Sdk2013Client:
        return self.sdk


class _Sdk2013Supervisor:
    def __init__(self, client: _Sdk2013Client) -> None:
        self.processor = SdkWriteProcessor(_Sdk2013Handle(client))  # type: ignore[arg-type]

    async def process_sdk_job(self, records, *, request_id, before_hy_invocation):
        return await self.processor.process_sdk_job(
            records, request_id=request_id, before_hy_invocation=before_hy_invocation
        )

    async def inquire_write_outcome(self, _request_id):
        return None


class _Turns:
    """Evidence for one conversation batch: a chat turn, or a coding turn with tools."""

    def __init__(self, roles: tuple[str, ...]) -> None:
        self.records = {
            f"e-{index}": materialize_write_input(
                EvidenceInput(
                    user_id="local",
                    source_id="cursor",
                    conversation_id="sess-1",
                    message_id=f"msg-{index}",
                    role=role,
                    written_by=WrittenBy.PERSON,
                    created_at=datetime(2026, 10, 1, tzinfo=UTC),
                    verbatim=f"{role} says something worth keeping ({index})",
                    origin_meta={EvidenceOrigin.FILE: OriginMetadata()},
                )
            )
            for index, role in enumerate(roles)
        }

    def get(self, evidence_id: str):
        return self.records[evidence_id]

    def request(self) -> WriteRequest:
        return WriteRequest(
            internal_session_id="sess-1",
            batch_identity=BatchIdentity(internal_session_id="sess-1", first_round=1, last_round=1),
            evidence_ids=list(self.records),
            idempotency_key="idem-000",
            source_id="cursor",
        )


def _outage_then_recovery(tmp_path, roles: tuple[str, ...], failure) -> tuple[list, list]:
    clock = _Clock()
    backoff = ModelBackoff(clock=clock)
    calls: list[float] = []

    def provider(request: httpx.Request) -> httpx.Response:
        calls.append(clock.now)
        if clock.now < 60.0:
            return failure(request)
        return httpx.Response(200)

    client = _Sdk2013Client(
        BackedOffModelTransport(backoff, profile_id="p:1", inner=httpx.MockTransport(provider))
    )
    turns = _Turns(roles)
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    queue.submit(turns.request())
    drain = DurableWriteDrain(
        queue, execute=_never_execute, owner="outage", model_claim_allowance=backoff.allowance
    )
    seen: list[tuple[str, int, int, str | None]] = []
    for second in range(120):
        clock.now = float(second)
        for job_id in drain.claim(limit=1):
            asyncio.run(
                execute_job(
                    job_id,
                    queue=queue,
                    supervisor=_Sdk2013Supervisor(client),
                    evidence=turns,
                    owner="outage",
                    model_failures=backoff.attempt,
                )
            )
            seen.append(_jobs(path)[0][1:])
    assert all(len(sent) == len(roles) for sent in client.messages)
    return calls, seen


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("provider timed out", request=request)


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (lambda _request: httpx.Response(401), "model_auth_failed"),
        (lambda _request: httpx.Response(503), "model_server_error"),
        (_timeout, "model_timeout"),
    ],
    ids=["401", "503", "timeout"],
)
def test_a_reconcile_model_failure_waits_for_the_provider_and_completes_after_it(
    tmp_path, failure, code
) -> None:
    """hy-memory 2.0.13 fails the write when reconcile cannot reach its model.

    The breaker saw the provider fail, so the job waits in place without
    spending a retry, is tried once per pause, and lands after recovery.
    """
    calls, seen = _outage_then_recovery(tmp_path, ("user", "assistant"), failure)
    assert calls == [0.0, 5.0, 15.0, 35.0, 75.0]
    assert seen[:-1] == [("pending", attempt, 0, code) for attempt in range(1, 5)]
    assert seen[-1] == ("completed", 4, 0, None)


def test_a_coding_batch_whose_extractor_fails_at_the_provider_waits_like_chat(tmp_path) -> None:
    """Tool turns go down the SDK's coding path; its `LLM_ERROR` waits the same way."""
    calls, seen = _outage_then_recovery(
        tmp_path, ("user", "assistant", "tool", "assistant"), lambda _request: httpx.Response(502)
    )
    assert calls == [0.0, 5.0, 15.0, 35.0, 75.0]
    assert seen[:-1] == [("pending", attempt, 0, "model_server_error") for attempt in range(1, 5)]
    assert seen[-1] == ("completed", 4, 0, None)


@pytest.mark.parametrize(
    ("reply", "code"),
    [
        ("", "hy_sdk_reconcile_empty_response"),
        ("I am not sure what you mean.", "hy_sdk_reconcile_json_parse_failed"),
    ],
    ids=["empty", "unparseable"],
)
def test_a_reconcile_reply_the_sdk_cannot_use_is_retried_then_failed_with_its_cause(
    tmp_path, reply, code
) -> None:
    """HTTP 200 with nothing usable is not a provider failure: 3 retries, then `failed`.

    The job's `error_code` names the SDK's cause instead of a bare
    `hy_sdk_write_failed`, and the provider is never paused.
    """
    backoff = ModelBackoff(clock=_Clock())
    client = _Sdk2013Client(
        BackedOffModelTransport(
            backoff, profile_id="p:1", inner=httpx.MockTransport(lambda _r: httpx.Response(200))
        )
    )
    client.reply = reply
    turns = _Turns(("user", "assistant"))
    path = tmp_path / "one-memory.sqlite3"
    queue = DurableWriteQueue(path)
    queue.submit(turns.request())
    drain = DurableWriteDrain(queue, execute=_never_execute, owner="bad-reply")
    now = datetime.now(UTC)
    for offset in (0, 40, 150, 400):
        for job_id in drain.claim(limit=1, now=now + timedelta(seconds=offset)):
            asyncio.run(
                execute_job(
                    job_id,
                    queue=queue,
                    supervisor=_Sdk2013Supervisor(client),
                    evidence=turns,
                    owner="bad-reply",
                    model_failures=backoff.attempt,
                )
            )
    assert _jobs(path)[0][1:] == ("failed", 3, 3, code)
    assert queue.get("idem-000").error_code == code
    assert len(client.messages) == 4
    assert backoff.allowance() is None


@pytest.mark.parametrize(
    ("result", "code"),
    [
        (
            {"agent_error_code": "RECONCILE_FAILED", "reconcile_error_code": "LLM_UNAVAILABLE"},
            "hy_sdk_reconcile_llm_unavailable",
        ),
        (
            {"agent_error_code": "RECONCILE_FAILED", "reconcile_error_code": "RECONCILE_EXCEPTION"},
            "hy_sdk_reconcile_exception",
        ),
        ({"agent_error_code": "RECONCILE_FAILED"}, "hy_sdk_reconcile_failed"),
        ({"agent_error_code": "EMBEDDING_FAILED"}, "hy_sdk_embedding_failed"),
        ({"agent_error_code": "JSON_PARSE_FAILED"}, "hy_sdk_json_parse_failed"),
        ({"agent_error_code": "provider said: bad key sk-123"}, "hy_sdk_write_failed"),
        ({"agent_error_code": 502}, "hy_sdk_write_failed"),
        ({}, "hy_sdk_write_failed"),
    ],
)
def test_a_failed_sdk_write_keeps_only_the_sdks_stable_code(result, code) -> None:
    assert sdk_write_failure_code({"success": False, **result}) == code
