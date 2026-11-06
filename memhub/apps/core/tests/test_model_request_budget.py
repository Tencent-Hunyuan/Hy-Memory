import asyncio
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from one_memory.config import Settings
from one_memory.model_request_budget import (
    BudgetedModelTransport,
    ModelRequestBudget,
    ModelRequestBudgetError,
)


def budget(tmp_path, limit=2):
    return ModelRequestBudget(
        tmp_path / "one-memory.sqlite3",
        request_limit=limit,
        body_byte_limit=256,
        output_token_limit=16,
    )


def payload():
    return json.dumps({"max_tokens": 16, "messages": [{"content": "private-text"}]}).encode()


def test_persists_failed_request_reservations_without_content(tmp_path):
    first = budget(tmp_path)
    first.reserve(payload())
    again = budget(tmp_path)
    again.reserve(payload())
    with pytest.raises(ModelRequestBudgetError, match="exhausted"):
        again.reserve(payload())
    with sqlite3.connect(again.database) as db:
        rows = db.execute("SELECT * FROM model_request_budget_events").fetchall()
    assert len(rows) == 2
    assert b"private-text" not in again.database.read_bytes()


def test_parallel_leases_cannot_exceed_request_limit(tmp_path):
    store = budget(tmp_path, limit=3)

    def reserve(_):
        try:
            store.reserve(payload())
            return True
        except ModelRequestBudgetError:
            return False

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(reserve, range(12))) == 3


@pytest.mark.parametrize(
    "content",
    [
        b"invalid",
        b"[]",
        b"{}",
        b'{"max_tokens":true}',
        b'{"max_tokens":17}',
        b'{"max_tokens":0}',
        b"{" + b" " * 257 + b"}",
    ],
)
def test_unknown_or_excessive_output_and_body_are_denied(tmp_path, content):
    with pytest.raises(ModelRequestBudgetError):
        budget(tmp_path).reserve(content)


def test_transport_counts_physical_failure_and_blocks_before_second_send(tmp_path):
    sent = []

    def response(request):
        sent.append(request.content)
        return httpx.Response(500)

    async def scenario():
        transport = BudgetedModelTransport(
            budget(tmp_path, limit=1), inner=httpx.MockTransport(response)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await client.post("https://example.test/chat/completions", content=payload())
            assert result.status_code == 500
            with pytest.raises(ModelRequestBudgetError):
                await client.post("https://example.test/chat/completions", content=payload())

    asyncio.run(scenario())
    assert len(sent) == 1


def test_runtime_limit_is_opt_in_and_bounds_are_validated():
    assert Settings(_env_file=None).model_request_limit is None
    with pytest.raises(ValueError):
        Settings(_env_file=None, model_request_limit=0)


@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
def test_token_field_variants_are_budgeted(tmp_path, field):
    store = budget(tmp_path)
    store.reserve(json.dumps({field: 12}).encode())
    with sqlite3.connect(store.database) as db:
        assert db.execute("SELECT output_tokens FROM model_request_budget_events").fetchone() == (
            12,
        )


@pytest.mark.parametrize(
    "body",
    [
        {"max_completion_tokens": True},
        {"max_completion_tokens": 0},
        {"max_completion_tokens": 17},
        {"max_tokens": 5, "max_completion_tokens": 17},
        {"max_tokens": 17, "max_completion_tokens": 5},
        {"max_tokens": 5, "max_completion_tokens": None},
    ],
)
def test_either_token_field_must_be_valid_and_within_budget(tmp_path, body):
    with pytest.raises(ModelRequestBudgetError):
        budget(tmp_path).reserve(json.dumps(body).encode())


def test_both_fields_account_for_the_largest_limit(tmp_path):
    store = budget(tmp_path)
    store.reserve(b'{"max_tokens":5,"max_completion_tokens":12}')
    with sqlite3.connect(store.database) as db:
        assert db.execute("SELECT output_tokens FROM model_request_budget_events").fetchone() == (
            12,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
async def test_real_sdk_request_through_budgeted_transport(tmp_path, field):
    from hy_memory.agent.llm_provider import LLMProvider
    from hy_memory.config import LLMConfig, MemoryConfig

    seen = []

    def handle(request):
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ok"},
                    }
                ],
            },
        )

    store = ModelRequestBudget(
        tmp_path / "budget.sqlite3", request_limit=2, body_byte_limit=4096, output_token_limit=200
    )
    config = MemoryConfig()
    config.llm = LLMConfig(
        api_key="test",
        base_url="https://example.test/v1",
        output_token_parameter=field,
        max_retries=1,
    )
    config.http_transport_factory = lambda: BudgetedModelTransport(
        store, inner=httpx.MockTransport(handle)
    )
    assert (await LLMProvider(config).complete("hello", max_tokens=123)).content == "ok"
    assert seen[0][field] == 123
    with sqlite3.connect(store.database) as db:
        assert db.execute("SELECT output_tokens FROM model_request_budget_events").fetchone() == (
            123,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"max_tokens": 3, "max_completion_tokens": 17},
        {"max_tokens": 17, "max_completion_tokens": 3},
        {"max_completion_tokens": True},
    ],
)
async def test_invalid_either_field_never_reaches_model(tmp_path, body):
    sent = []
    store = budget(tmp_path)
    async with httpx.AsyncClient(
        transport=BudgetedModelTransport(
            store, inner=httpx.MockTransport(lambda req: sent.append(req) or httpx.Response(200))
        )
    ) as client:
        with pytest.raises(ModelRequestBudgetError):
            await client.post("https://example.test/chat/completions", json=body)
    assert not sent
    with sqlite3.connect(store.database) as db:
        assert db.execute("SELECT COUNT(*) FROM model_request_budget_events").fetchone() == (0,)
