"""Malformed provider indices must not attach one input's vector to another."""

import asyncio
import json
import logging

import httpx
import openai
import pytest

from hy_memory import MemoryConfig
from hy_memory.core.embed_service import EmbedService, EmbeddingIndexMappingError


@pytest.mark.parametrize(
    "indices", [[0, 0], [-1, 1], [0, 2], [None, 1], [0, 0.5], [False, 1]]
)
async def test_invalid_response_indices_are_rejected_without_cache(
    monkeypatch, indices
):
    calls = []

    def respond(request):
        calls.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "test-model",
                "data": [
                    {
                        "object": "embedding",
                        "embedding": [float(i), 1.0],
                        **({"index": index} if index is not None else {}),
                    }
                    for i, index in enumerate(indices)
                ],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        async with openai.AsyncOpenAI(
            api_key="synthetic-test-key",
            base_url="https://provider.invalid/v1",
            http_client=http,
        ) as client:
            config = MemoryConfig()
            config.embedder.embedding_dims = 2
            service = EmbedService(config)
            monkeypatch.setattr(service, "_get_client", lambda *_: client)
            with pytest.raises(ValueError, match="[Ii]ndex"):
                await service.embed_batch(
                    ["first input", "second input"], max_retries=0
                )
            assert not service._cache
            assert len(calls) == 1


async def test_out_of_order_valid_indices_keep_batch_and_queue_mapping(monkeypatch):
    calls = []

    def respond(request):
        inputs = json.loads(request.content)["input"]
        calls.append(inputs)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "test-model",
                "data": [
                    {
                        "object": "embedding",
                        "index": i,
                        "embedding": [float(int(text)), 1.0],
                    }
                    for i, text in reversed(list(enumerate(inputs)))
                ],
                "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        async with openai.AsyncOpenAI(
            api_key="synthetic-test-key",
            base_url="https://provider.invalid/v1",
            http_client=http,
        ) as client:
            config = MemoryConfig()
            config.embedder.embedding_dims = 2
            service = EmbedService(config)
            monkeypatch.setattr(service, "_get_client", lambda *_: client)
            service._embed_config.batch_size = 2
            assert await service.embed_batch(["10", "20", "30"]) == [
                [10.0, 1.0],
                [20.0, 1.0],
                [30.0, 1.0],
            ]
            assert await service.embed_batch(["30", "10"]) == [[30.0, 1.0], [10.0, 1.0]]
            assert len(calls) == 2
            service._embed_config.queue_batch_size = 2
            assert await asyncio.gather(
                service.embed_queued("40"), service.embed_queued("50")
            ) == [[40.0, 1.0], [50.0, 1.0]]
            assert len(calls) == 3


async def test_invalid_later_chunk_does_not_partially_populate_cache(monkeypatch):
    calls = []

    def respond(request):
        inputs = json.loads(request.content)["input"]
        calls.append(inputs)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "test-model",
                "data": [
                    {
                        "object": "embedding",
                        "index": i if len(calls) == 1 else 0,
                        "embedding": [float(int(text)), 1.0],
                    }
                    for i, text in enumerate(inputs)
                ],
                "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        async with openai.AsyncOpenAI(
            api_key="synthetic-test-key",
            base_url="https://provider.invalid/v1",
            http_client=http,
        ) as client:
            config = MemoryConfig()
            config.embedder.embedding_dims = 2
            service = EmbedService(config)
            service._embed_config.batch_size = 2
            monkeypatch.setattr(service, "_get_client", lambda *_: client)
            with pytest.raises(ValueError, match="[Ii]ndex"):
                await service.embed_batch(["10", "20", "30", "40"], max_retries=0)
            assert not service._cache
            assert len(calls) == 2


async def test_invalid_queued_batch_fails_all_waiters_without_caching(monkeypatch):
    calls = []

    def respond(request):
        inputs = json.loads(request.content)["input"]
        calls.append(inputs)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "test-model",
                "data": [
                    {"object": "embedding", "index": 0, "embedding": [float(i), 1.0]}
                    for i in range(len(inputs))
                ],
                "usage": {"prompt_tokens": len(inputs), "total_tokens": len(inputs)},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        async with openai.AsyncOpenAI(
            api_key="synthetic-test-key",
            base_url="https://provider.invalid/v1",
            http_client=http,
        ) as client:
            config = MemoryConfig()
            config.embedder.embedding_dims = 2
            config.embedder.max_retries = 0
            service = EmbedService(config)
            service._embed_config.queue_batch_size = 2
            service._embed_config.queue_batch_window_ms = 1000
            monkeypatch.setattr(service, "_get_client", lambda *_: client)
            results = await asyncio.wait_for(
                asyncio.gather(
                    service.embed_queued("first input"),
                    service.embed_queued("second input"),
                    return_exceptions=True,
                ),
                timeout=2,
            )
            assert all(
                isinstance(result, ValueError) and "index" in str(result)
                for result in results
            )
            assert not service._cache
            assert len(calls) == 1


def _respond_with(indices, calls):
    def respond(request):
        calls.append(json.loads(request.content)["input"])
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "test-model",
                "data": [
                    {
                        "object": "embedding",
                        "embedding": [float(i), 1.0],
                        **({"index": index} if index is not None else {}),
                    }
                    for i, index in enumerate(indices)
                ],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    return respond


async def test_missing_indices_keep_response_order(monkeypatch, caplog):
    calls = []
    respond = _respond_with([None, None], calls)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        async with openai.AsyncOpenAI(
            api_key="synthetic-test-key",
            base_url="https://provider.invalid/v1",
            http_client=http,
        ) as client:
            config = MemoryConfig()
            config.embedder.embedding_dims = 2
            service = EmbedService(config)
            monkeypatch.setattr(service, "_get_client", lambda *_: client)
            with caplog.at_level(logging.DEBUG, logger="hy_memory.core.embed_service"):
                assert await service.embed_batch(["first", "second"]) == [
                    [0.0, 1.0],
                    [1.0, 1.0],
                ]
            assert "no index" in caplog.text
            assert len(calls) == 1


@pytest.mark.parametrize(
    "indices",
    [
        pytest.param([0, None], id="partial"),
        pytest.param([None, 0], id="partial-first-missing"),
        pytest.param([1, 1], id="duplicate"),
        pytest.param([0, 5], id="out-of-range"),
        pytest.param([True, 0], id="bool"),
        pytest.param(["0", 1], id="non-int"),
    ],
)
async def test_malformed_index_mapping_is_not_retried(monkeypatch, indices):
    calls = []
    respond = _respond_with(indices, calls)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
        async with openai.AsyncOpenAI(
            api_key="synthetic-test-key",
            base_url="https://provider.invalid/v1",
            http_client=http,
        ) as client:
            config = MemoryConfig()
            config.embedder.embedding_dims = 2
            config.embedder.max_retries = 6
            config.embedder.retry_delay = 0
            service = EmbedService(config)
            monkeypatch.setattr(service, "_get_client", lambda *_: client)
            with pytest.raises(EmbeddingIndexMappingError):
                await service.embed_batch(["first", "second"])
            assert len(calls) == 1
            assert service._last_embed_meta["retryable"] is False
            assert not service._cache
