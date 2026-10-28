"""HTTP status must win over misleading words in an embedding error body."""

import asyncio
import json

import httpx
import openai
import pytest

from hy_memory.config import EmbedderConfig, MemoryConfig
from hy_memory.core.embed_service import EmbedService


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422, 408, 429, 500, 503])
@pytest.mark.parametrize("message", ["Invalid connection configuration", "Invalid timeout parameter"])
async def test_http_status_controls_embedding_retries(status, message):
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(status, json={
            "error": {"message": message, "type": "provider_error", "code": None},
        })

    config = MemoryConfig(embedder=EmbedderConfig(
        model="embedding-test", api_key="synthetic-key",
        base_url="https://embedding.example.test/v1",
        embedding_dims=2, max_retries=2, retry_delay=0,
    ))
    config.http_transport_factory = lambda: httpx.MockTransport(handle)
    service = EmbedService(config)
    try:
        with pytest.raises(openai.APIStatusError) as caught:
            await service.embed("synthetic text", use_cache=False)
        assert caught.value.status_code == status
        retryable = status in (408, 429) or status >= 500
        assert len(requests) == (3 if retryable else 1)
        assert all(request["input"] == ["synthetic text"] for request in requests)
        assert service._last_embed_meta["retryable"] is retryable
        assert service._last_embed_meta["attempts"] == len(requests)
        assert service._cache_len() == 0
    finally:
        for client in service._client_cache.values():
            await client.close()
        # Retry eviction schedules close() in a separate task.
        await asyncio.sleep(0)


async def test_transport_failure_still_retries_and_can_recover():
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={
            "object": "list", "model": "embedding-test",
            "data": [{"object": "embedding", "index": 0, "embedding": [1.0, 0.0]}],
            "usage": {"prompt_tokens": 2, "total_tokens": 2},
        })

    config = MemoryConfig(embedder=EmbedderConfig(
        model="embedding-test", api_key="synthetic-key",
        base_url="https://embedding.example.test/v1",
        embedding_dims=2, max_retries=2, retry_delay=0,
    ))
    config.http_transport_factory = lambda: httpx.MockTransport(handle)
    service = EmbedService(config)
    try:
        assert await service.embed("synthetic text", use_cache=False) == [1.0, 0.0]
        assert len(requests) == 2
        assert service._last_embed_meta["attempts"] == 2
    finally:
        for client in service._client_cache.values():
            await client.close()
        await asyncio.sleep(0)
