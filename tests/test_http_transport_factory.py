import asyncio
from types import SimpleNamespace

import httpx
import openai

from hy_memory.agent.llm_provider import LLMProvider
from hy_memory.config import MemoryConfig
from hy_memory.core.embed_service import EmbedService
from hy_memory.utils.model_call_context import current_model_call_stage


class _Transport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")


def test_embed_service_uses_runtime_transport_factory(monkeypatch) -> None:
    config = MemoryConfig()
    transports = []
    config.http_transport_factory = lambda: transports.append(_Transport()) or transports[-1]
    captured = {}

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kwargs: captured.update(kwargs) or SimpleNamespace(),
    )

    EmbedService(config)._get_client("https://example.test/v1", "key", 30)

    assert len(transports) == 1
    assert isinstance(captured["http_client"], httpx.AsyncClient)
    asyncio.run(captured["http_client"].aclose())


def test_llm_provider_uses_runtime_transport_factory(monkeypatch) -> None:
    config = MemoryConfig()
    config.llm.base_url = "https://example.test/v1"
    config.llm.api_key = "key"
    config.llm.model = "model"
    transports = []
    config.http_transport_factory = lambda: transports.append(_Transport()) or transports[-1]
    captured = {}

    class Completions:
        async def create(self, **kwargs):
            return SimpleNamespace(
                id="response",
                model="model",
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="ok", tool_calls=None),
                        finish_reason="stop",
                    )
                ],
                usage=SimpleNamespace(
                    total_tokens=1,
                    prompt_tokens=1,
                    completion_tokens=0,
                    completion_tokens_details=None,
                ),
            )

    class Client:
        chat = SimpleNamespace(completions=Completions())

        async def close(self):
            await captured["http_client"].aclose()

    monkeypatch.setattr(
        openai,
        "AsyncOpenAI",
        lambda **kwargs: captured.update(kwargs) or Client(),
    )

    provider = LLMProvider(config)
    response = asyncio.run(
        provider._chat_completions([{"role": "user", "content": "hello"}], max_tokens=8)
    )

    assert response.content == "ok"
    assert len(transports) == 1
    assert isinstance(captured["http_client"], httpx.AsyncClient)
    asyncio.run(provider.close())


def test_llm_provider_exposes_audit_stage_during_physical_call(monkeypatch) -> None:
    config = MemoryConfig()
    provider = LLMProvider(config)
    observed = []

    async def call(**_kwargs):
        observed.append(current_model_call_stage())
        return SimpleNamespace(
            content="ok",
            tokens_used=3,
            prompt_tokens=2,
            completion_tokens=1,
            tool_calls=[],
        )

    monkeypatch.setattr(provider, "_call_llm", call)

    asyncio.run(provider.complete("hello", audit_stage="coding_judge"))

    assert observed == ["coding_judge"]
    assert current_model_call_stage() == "other"
