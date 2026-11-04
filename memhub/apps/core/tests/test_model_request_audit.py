import asyncio
import json
import sqlite3

import httpx
import pytest

from one_memory.model_request_audit import (
    AuditedModelTransport,
    ModelRequestAuditStore,
    audit_precipitation_request,
)


def _transport(tmp_path, response):
    store = ModelRequestAuditStore(tmp_path / "one-memory.sqlite3")
    return store, AuditedModelTransport(
        store,
        profile_id="profile-1",
        profile_revision=2,
        provider_kind="openai_compatible",
        model="deepseek-flash",
        inner=httpx.MockTransport(response),
    )


def test_records_one_physical_request_with_provider_usage_and_no_content(tmp_path, monkeypatch):
    private = "private conversation text"
    monkeypatch.setattr(
        "one_memory.model_request_audit._current_prompt_stage",
        lambda: "coding_judge",
    )

    def response(_request):
        return httpx.Response(
            200,
            json={
                "id": "provider-response-1",
                "usage": {
                    "prompt_tokens": 120,
                    "completion_tokens": 30,
                    "total_tokens": 150,
                    "prompt_tokens_details": {"cached_tokens": 80},
                    "completion_tokens_details": {"reasoning_tokens": 10},
                },
            },
        )

    store, transport = _transport(tmp_path, response)

    async def scenario():
        async with httpx.AsyncClient(transport=transport) as client:
            with audit_precipitation_request("memory-request-1"):
                result = await client.post(
                    "https://example.test/chat/completions",
                    json={"model": "deepseek-flash", "messages": [{"content": private}]},
                )
                assert result.status_code == 200
                assert result.json()["id"] == "provider-response-1"

    asyncio.run(scenario())
    report = store.report(request_id="memory-request-1")
    assert report["summary"] == {
        "request_count": 1,
        "succeeded": 1,
        "failed": 0,
        "in_flight": 0,
        "prompt_tokens": 120,
        "completion_tokens": 30,
        "total_tokens": 150,
        "cached_prompt_tokens": 80,
        "reasoning_tokens": 10,
        "avg_prompt_tokens": 120.0,
        "avg_completion_tokens": 30.0,
    }
    row = report["requests"][0]
    assert row["provider_request_id"] == "provider-response-1"
    assert row["request_kind"] == "chat_completion"
    assert row["prompt_stage"] == "coding_judge"
    assert row["profile_revision"] == 2
    assert report["by_minute"][0]["prompt_stage"] == "coding_judge"
    assert report["by_minute"][0]["total_tokens"] == 150
    assert report["by_minute"][0]["avg_prompt_tokens"] == 120.0
    assert private.encode() not in store.database.read_bytes()


def test_requests_outside_precipitation_are_not_recorded(tmp_path):
    store, transport = _transport(tmp_path, lambda _request: httpx.Response(200, json={}))

    async def scenario():
        async with httpx.AsyncClient(transport=transport) as client:
            await client.post("https://example.test/chat/completions", json={})

    asyncio.run(scenario())
    assert store.report()["summary"]["request_count"] == 0


def test_precipitation_context_reaches_the_sdk_worker_thread(tmp_path):
    store, transport = _transport(
        tmp_path,
        lambda _request: httpx.Response(
            200,
            json={"usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3}},
        ),
    )

    def worker():
        async def request():
            async with httpx.AsyncClient(transport=transport) as client:
                await client.post("https://example.test/chat/completions", json={})

        asyncio.run(request())

    async def scenario():
        with audit_precipitation_request("threaded-memory-request"):
            await asyncio.to_thread(worker)

    asyncio.run(scenario())
    assert store.report(request_id="threaded-memory-request")["summary"]["total_tokens"] == 3


def test_failure_is_recorded_without_error_message(tmp_path):
    secret = "provider leaked private detail"

    def response(_request):
        raise httpx.ConnectError(secret)

    store, transport = _transport(tmp_path, response)

    async def scenario():
        async with httpx.AsyncClient(transport=transport) as client:
            with audit_precipitation_request("memory-request-failed"):
                with pytest.raises(httpx.ConnectError):
                    await client.post("https://example.test/embeddings", json={})

    asyncio.run(scenario())
    report = store.report(request_id="memory-request-failed")
    row = report["requests"][0]
    assert row["outcome"] == "failed"
    assert row["error_type"] == "ConnectError"
    assert row["request_kind"] == "embedding"
    assert secret.encode() not in store.database.read_bytes()


def test_missing_usage_stays_unknown_in_detail_and_zero_in_summary(tmp_path):
    store, transport = _transport(
        tmp_path,
        lambda _request: httpx.Response(200, content=json.dumps({"id": "r"}).encode()),
    )

    async def scenario():
        async with httpx.AsyncClient(transport=transport) as client:
            with audit_precipitation_request("memory-request-no-usage"):
                await client.post("https://example.test/chat/completions", json={})

    asyncio.run(scenario())
    report = store.report(request_id="memory-request-no-usage")
    assert report["requests"][0]["total_tokens"] is None
    assert report["summary"]["total_tokens"] == 0


def test_store_schema_matches_migration_and_supports_parallel_starts(tmp_path):
    store = ModelRequestAuditStore(tmp_path / "one-memory.sqlite3")

    async def start(index):
        return await asyncio.to_thread(
            store.start,
            request_id=f"request-{index}",
            profile_id="profile",
            profile_revision=1,
            provider_kind="openai_compatible",
            model="model",
            request_kind="chat_completion",
            prompt_stage="other",
        )

    async def scenario():
        return await asyncio.gather(*(start(index) for index in range(8)))

    ids = asyncio.run(scenario())
    assert len(set(ids)) == 8
    with sqlite3.connect(store.database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM model_request_audit").fetchone() == (8,)


def test_sdk_write_records_each_retry_across_the_real_sdk_loop(tmp_path):
    from hy_memory.client import _LoopThread

    from one_memory.sdk_write_processor import _write

    attempts = 0

    def provider(_request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            503 if attempts == 1 else 200,
            json={"usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}},
        )

    store, transport = _transport(tmp_path, provider)
    loop = _LoopThread()

    async def model_calls():
        async with httpx.AsyncClient(transport=transport) as client:
            for _ in range(2):
                await client.post("https://example.test/chat/completions", json={})

    try:
        asyncio.run(_write("sdk-real-loop", lambda: loop.run(model_calls())))
    finally:
        loop.stop()
    totals = store.report(request_id="sdk-real-loop")["summary"]
    assert (totals["request_count"], totals["failed"], totals["succeeded"]) == (2, 1, 1)
    assert totals["total_tokens"] == 30


def test_streaming_usage_is_observed_without_buffering_or_saving_answer(tmp_path):
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"choices":[{"delta":{"content":"private answer"}}]}\n\n'
            yield b'data: {"id":"stream-id","choices":[],"usage":{"prompt_tokens":10,'
            yield b'"completion_tokens":2,"total_tokens":12,"prompt_cache_hit_tokens":8}}\n\n'
            yield b"data: [DONE]\n\n"

    store, transport = _transport(
        tmp_path,
        lambda _: httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            stream=Stream(),
        ),
    )

    async def scenario():
        async with httpx.AsyncClient(transport=transport) as client:
            with audit_precipitation_request("stream"):
                async with client.stream(
                    "POST", "https://example.test/chat/completions"
                ) as response:
                    assert store.report()["summary"]["in_flight"] == 1
                    chunks = response.aiter_raw()
                    assert b"private answer" in await anext(chunks)
                    assert store.report()["summary"]["in_flight"] == 1
                    async for _ in chunks:
                        pass

    asyncio.run(scenario())
    row = store.report()["requests"][0]
    assert (row["total_tokens"], row["cached_prompt_tokens"], row["outcome"]) == (
        12,
        8,
        "succeeded",
    )
    assert b"private answer" not in store.database.read_bytes()


def _daily_service(tmp_path):
    from one_memory.model_usage import ModelUsageService

    store = ModelRequestAuditStore(tmp_path / "one-memory.sqlite3")
    with sqlite3.connect(store.database) as db:
        db.execute(
            "CREATE TABLE model_profile_versions (profile_id TEXT, revision INT, base_url TEXT)"
        )
        db.executemany(
            "INSERT INTO model_profile_versions VALUES (?,1,?)",
            [
                ("deepseek", "https://api.deepseek.com"),
                ("other", "https://other.test/v1"),
            ],
        )
    return store, ModelUsageService(store.database, tmp_path)


def _record_daily(store, *, profile="deepseek", model="deepseek-flash", at, usage=True):
    audit_id = store.start(
        request_id="r",
        profile_id=profile,
        profile_revision=1,
        provider_kind="openai_compatible",
        model=model,
        request_kind="chat_completion",
        prompt_stage="other",
    )
    if usage:
        store.finish(
            audit_id,
            outcome="succeeded",
            http_status=200,
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            cached_prompt_tokens=80,
        )
    with sqlite3.connect(store.database) as db:
        db.execute("UPDATE model_request_audit SET started_at=? WHERE audit_id=?", (at, audit_id))


def test_daily_report_uses_local_calendar_all_rows_and_preserves_unknown_usage(tmp_path):
    from datetime import datetime

    store, service = _daily_service(tmp_path)
    for _ in range(205):
        _record_daily(store, at="2026-11-03T16:01:00+00:00")
    _record_daily(store, at="2026-11-03T15:59:00+00:00")
    _record_daily(store, at="2026-11-04T01:00:00+00:00", usage=False)
    report = service.report(
        days=7, timezone="Asia/Shanghai", now=datetime.fromisoformat("2026-11-04T04:00:00+00:00")
    )
    today, yesterday = report["days"][:2]
    assert today["request_count"] == 206 and yesterday["request_count"] == 1
    assert today["total_tokens"] == 205 * 120
    assert today["unknown_usage_requests"] == today["in_flight"] == 1
    assert report["days"][-1]["coverage"] == "unrecorded"
    assert today["costs"][0]["estimated_min"] == "0.02082800"
    assert today["unpriced_requests"] == 1


def test_custom_vendor_prices_currencies_and_links_survive_restart(tmp_path):
    from datetime import datetime

    from one_memory.model_usage import ModelPricing, ModelUsageService

    store, service = _daily_service(tmp_path)
    _record_daily(store, at="2026-11-04T02:00:00+00:00")
    _record_daily(store, profile="other", model="vendor-model", at="2026-11-04T02:00:00+00:00")
    price = ModelPricing(
        model="vendor-model",
        currency="USD",
        input_per_million="2",
        cached_input_per_million="0.5",
        output_per_million="8",
        billing_url="https://vendor.test/billing",
    )
    service.set_pricing("other", price)
    service = ModelUsageService(store.database, tmp_path)
    assert service.settings().pricing["other"] == price
    report = service.report(
        days=1, timezone="UTC", now=datetime.fromisoformat("2026-11-04T04:00:00+00:00")
    )
    assert [cost["currency"] for cost in report["summary"]["costs"]] == ["CNY", "USD"]
    assert report["summary"]["costs"][1]["estimated_max"] == "0.00024000"
    deepseek = report["summary"]["costs"][0]
    assert deepseek["estimated_max"] == str(
        __import__("decimal").Decimal(deepseek["estimated_min"]) * 2
    )
    filtered = service.report(
        days=1,
        timezone="UTC",
        profile_id="other",
        now=datetime.fromisoformat("2026-11-04T04:00:00+00:00"),
    )
    assert filtered["summary"]["request_count"] == 1
    service.set_pricing("other", price.model_copy(update={"model": "old-model"}))
    report = service.report(
        days=1, timezone="UTC", now=datetime.fromisoformat("2026-11-04T04:00:00+00:00")
    )
    assert report["summary"]["unpriced_requests"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"input_per_million": "NaN"},
        {"output_per_million": "Infinity"},
        {"input_per_million": "-1"},
        {"billing_url": "javascript:alert(1)"},
        {"billing_url": "https://secret@vendor.test/usage"},
        {"currency": "usd"},
    ],
)
def test_pricing_rejects_invalid_rates_and_unsafe_links(changes):
    from pydantic import ValidationError

    from one_memory.model_usage import ModelPricing

    with pytest.raises(ValidationError):
        ModelPricing(model="model", **changes)


def test_application_transport_counts_unscoped_calls_but_not_local_budget_refusals(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from one_memory.api import _usage_transport
    from one_memory.model_request_budget import ModelRequestBudget, ModelRequestBudgetError

    store = ModelRequestAuditStore(tmp_path / "one-memory.sqlite3")
    monkeypatch.setattr(
        httpx,
        "AsyncHTTPTransport",
        lambda **_: httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "usage": {
                        "prompt_tokens": 2,
                        "completion_tokens": 1,
                        "total_tokens": 3,
                    }
                },
            )
        ),
    )
    app = SimpleNamespace(state=SimpleNamespace(model_request_audit_store=store))
    profile = SimpleNamespace(
        profile_id="p", revision=1, provider_kind="openai_compatible", model="m"
    )
    budget = ModelRequestBudget(
        store.database, request_limit=1, body_byte_limit=1000, output_token_limit=100
    )

    async def scenario():
        async with httpx.AsyncClient(
            transport=_usage_transport(app, profile, budget=budget)
        ) as client:
            await client.post("https://example.test/chat/completions", json={"max_tokens": 10})
            with pytest.raises(ModelRequestBudgetError):
                await client.post("https://example.test/chat/completions", json={"max_tokens": 10})

    asyncio.run(scenario())
    assert store.report()["summary"]["request_count"] == 1


def _balance_service(
    handler, *, base_url="https://api.deepseek.com/v1", secret="test-provider-key"
):
    from types import SimpleNamespace

    from one_memory.provider_balance import ProviderBalanceService

    profile = SimpleNamespace(
        profile_id="profile", revision=1, provider_kind="openai_compatible", base_url=base_url
    )

    class Profiles:
        def resolve_snapshot_secret(self, *_args):
            return secret

    clock = [0.0]
    service = ProviderBalanceService(
        Profiles(), object(), transport=httpx.MockTransport(handler), clock=lambda: clock[0]
    )
    return service, profile, clock


def test_official_balance_reads_real_fields_caches_and_does_not_enter_token_audit():
    calls = []

    def provider(request):
        calls.append(request)
        assert str(request.url) == "https://api.deepseek.com/user/balance"
        assert request.headers["Authorization"] == "Bearer test-provider-key"
        return httpx.Response(
            200,
            json={
                "is_available": True,
                "balance_infos": [
                    {
                        "currency": "CNY",
                        "total_balance": "100.123456",
                        "granted_balance": "10.12",
                        "topped_up_balance": "90.003456",
                    }
                ],
            },
        )

    service, profile, clock = _balance_service(provider)
    balance = service.get(profile)
    assert balance.state == "ready" and balance.is_available is True
    assert balance.model_dump(mode="json")["balances"][0]["total_balance"] == "100.123456"
    assert "test-provider-key" not in balance.model_dump_json()
    assert balance.checked_at is not None
    assert service.get(profile) is balance
    assert service.get(profile, refresh=True) is balance
    assert len(calls) == 1
    clock[0] = 6
    service.get(profile, refresh=True)
    assert len(calls) == 2
    profile.revision = 2
    service.get(profile)
    assert len(calls) == 3


@pytest.mark.parametrize(
    "base_url",
    [
        "https://proxy.example/v1",
        "https://api.deepseek.com.attacker.example",
        "http://api.deepseek.com",
        "https://user@api.deepseek.com",
        "https://api.deepseek.com:1234",
        "https://api.deepseek.com:bad",
    ],
)
def test_balance_never_sends_credentials_to_custom_or_unsafe_endpoints(base_url):
    def forbidden(_request):
        raise AssertionError("credentials must not be sent")

    service, profile, _clock = _balance_service(forbidden, base_url=base_url)
    assert service.get(profile).state == "unsupported"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(401, json={"error": "provider-private-detail"}),
        httpx.Response(429, json={"error": "provider-private-detail"}),
        httpx.Response(302, headers={"location": "https://attacker.example"}),
        httpx.Response(
            200,
            json={
                "is_available": True,
                "balance_infos": [{"currency": "CNY", "total_balance": "NaN"}],
            },
        ),
        httpx.Response(200, content=b"not json"),
    ],
)
def test_balance_failures_remain_unknown_and_do_not_leak_provider_details(response):
    service, profile, _clock = _balance_service(lambda _: response)
    balance = service.get(profile)
    assert balance.state == "unavailable"
    assert balance.is_available is None and not balance.balances
    assert "provider-private-detail" not in balance.model_dump_json()


def test_balance_missing_key_does_not_attempt_a_provider_request():
    service, profile, _clock = _balance_service(lambda _: pytest.fail("no key"), secret=None)
    assert service.get(profile).state == "credentials_unavailable"
