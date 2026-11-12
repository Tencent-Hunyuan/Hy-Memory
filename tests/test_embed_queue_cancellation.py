"""A caller leaving a shared embedding batch must not abort peer writes."""

import asyncio
import json

import httpx
import openai
import pytest

from hy_memory.config import MemoryConfig
from hy_memory.core.embed_service import EmbedService


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_status", [200, 500], ids=["success", "failure"])
@pytest.mark.parametrize("batch_size", [2, 8], ids=["full", "timer"])
@pytest.mark.parametrize("cancel_index", [0, 1], ids=["first", "last"])
async def test_cancelled_waiter_does_not_abort_shared_http_batch(
    batch_size, cancel_index, provider_status,
):
    started = asyncio.Event()
    release = asyncio.Event()
    requests = []

    async def handle(request):
        inputs = json.loads(request.content)["input"]
        requests.append(inputs)
        started.set()
        await release.wait()
        if provider_status != 200:
            return httpx.Response(provider_status, json={"error": {"message": "provider failed"}})
        return httpx.Response(200, json={
            "object": "list", "model": "test-embedding",
            "data": [
                {"object": "embedding", "index": i, "embedding": [float(i), 1.0, 2.0]}
                for i in range(len(inputs))
            ],
            "usage": {"prompt_tokens": 2, "total_tokens": 2},
        })

    config = MemoryConfig.from_dict({"embedder": {
        "provider": "openai", "model": "test-embedding", "api_key": "test-key",
        "base_url": "https://embedding.test/v1", "embedding_dims": 3,
        "max_retries": 0, "timeout": 2,
    }})
    config.http_transport_factory = lambda: httpx.MockTransport(handle)
    service = EmbedService(config)
    service._embed_config.queue_batch_size = batch_size
    service._embed_config.queue_batch_window_ms = 10
    tasks = [asyncio.create_task(service.embed_queued(text)) for text in ("one", "two")]
    try:
        await asyncio.wait_for(started.wait(), 10)
        tasks[cancel_index].cancel()
        with pytest.raises(asyncio.CancelledError):
            await tasks[cancel_index]
        release.set()
        survivor = 1 - cancel_index
        if provider_status == 200:
            assert await asyncio.wait_for(tasks[survivor], 2) == [float(survivor), 1.0, 2.0]
            # The successful caller caches its vector without a second request.
            assert await service.embed_queued(("one", "two")[survivor]) == [float(survivor), 1.0, 2.0]
        else:
            with pytest.raises(openai.InternalServerError, match="provider failed"):
                await asyncio.wait_for(tasks[survivor], 2)
        assert requests == [["one", "two"]]
        assert not getattr(service, "_queue_send_tasks", set())
    finally:
        release.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        timers = [task for task in service._queue_flush_tasks.values() if task is not None]
        for task in timers:
            task.cancel()
        await asyncio.gather(*timers, return_exceptions=True)
        for client in service._client_cache.values():
            await client.close()
