import asyncio
import json

import httpx
import pytest

from one_memory.model_probe import ModelProber
from one_memory.model_profiles import ModelProfileStore
from one_memory.secrets import InMemorySecretStore


@pytest.mark.parametrize("status", [400, 401, 429])
def test_probe_retries_only_explicit_token_parameter_refusal(tmp_path, status):
    payload = {"model": "example", "max_tokens": 256, "messages": []}
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            return httpx.Response(
                status,
                json={
                    "error": {
                        "param": "max_tokens",
                        "code": "unsupported_parameter",
                        "message": "Use max_completion_tokens instead.",
                    }
                },
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": '{"memory_probe": true}',
                        }
                    }
                ]
            },
        )

    prober = ModelProber(
        store=ModelProfileStore(tmp_path / "probe.sqlite3"),
        secrets=InMemorySecretStore(),
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await prober._request_stage(
                client,
                "structured",
                "POST",
                "https://example.test/chat/completions",
                {},
                payload,
                require_structured_json=True,
            )

    result = asyncio.run(run())

    assert result.ok is (status == 400)
    assert len(requests) == (2 if status == 400 else 1)
    if status == 400:
        assert requests[1] == {"model": "example", "max_completion_tokens": 256, "messages": []}
    assert payload == {"model": "example", "max_tokens": 256, "messages": []}


def test_probe_does_not_retry_an_unrelated_parameter_error(tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            400,
            json={
                "error": {
                    "param": "response_format",
                    "message": "Unsupported parameter",
                }
            },
        )

    prober = ModelProber(
        store=ModelProfileStore(tmp_path / "probe.sqlite3"),
        secrets=InMemorySecretStore(),
    )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await prober._request_stage(
                client,
                "structured",
                "POST",
                "https://example.test/chat/completions",
                {},
                {"max_tokens": 256},
            )

    assert asyncio.run(run()).ok is False
    assert len(requests) == 1
