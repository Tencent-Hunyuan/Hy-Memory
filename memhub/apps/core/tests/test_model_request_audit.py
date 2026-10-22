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
