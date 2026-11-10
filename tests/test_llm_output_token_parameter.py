"""Verify the actual Chat Completions HTTP payload across public entry points."""
import json

import httpx
import pytest

from hy_memory.agent.llm_provider import LLMConfig as ProviderConfig, LLMProvider
from hy_memory.config import LLMConfig, MemoryConfig


def test_default_and_invalid_configuration(monkeypatch):
    monkeypatch.delenv("MEMORY_LLM_OUTPUT_TOKEN_PARAMETER", raising=False)
    assert LLMConfig().output_token_parameter == "max_tokens"
    monkeypatch.setenv("MEMORY_LLM_OUTPUT_TOKEN_PARAMETER", "max_completion_tokens")
    assert LLMConfig().output_token_parameter == "max_completion_tokens"
    with pytest.raises(ValueError, match="OUTPUT_TOKEN_PARAMETER"):
        LLMConfig(output_token_parameter="max_completions_tokens")


def test_existing_positional_parameters_are_unchanged():
    # Previously the ninth provider argument was timeout, not the new field.
    provider = ProviderConfig(None, "model", "key", "url", 0.5, 17, 2, 0.3, {"x": "y"}, {"z": 1})
    assert provider.timeout == 17 and provider.extra_body == {"z": 1}
    config = LLMConfig("openai", "model", "key", "url", 0.7, False, 42, 43, 44)
    assert config.agent_max_tokens == 43 and config.timeout == 44


def test_dict_configuration_and_validation():
    cfg = MemoryConfig.from_dict({"llm": {"output_token_parameter": "max_completion_tokens"}})
    assert cfg.to_dict()["llm"]["output_token_parameter"] == "max_completion_tokens"
    cfg.llm.output_token_parameter = "invalid"
    with pytest.raises(ValueError, match="output_token_parameter"):
        LLMProvider(cfg)


@pytest.mark.asyncio
@pytest.mark.parametrize("parameter", ["max_tokens", "max_completion_tokens"])
@pytest.mark.parametrize("entrypoint", ["complete", "complete_messages"])
async def test_real_sdk_request_uses_exactly_selected_field(parameter, entrypoint):
    seen = []
    def handle(request):
        payload = json.loads(request.content)
        seen.append(payload)
        # Simulate an endpoint rejecting the wrong token parameter.
        other = "max_completion_tokens" if parameter == "max_tokens" else "max_tokens"
        if other in payload or parameter not in payload:
            return httpx.Response(400, json={"error": {"message": "unsupported field"}})
        return httpx.Response(200, json={"id": "chatcmpl-test", "object": "chat.completion", "created": 1,
            "model": "test", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
    cfg = MemoryConfig()
    cfg.llm = LLMConfig(api_key="test", base_url="https://example.test/v1", output_token_parameter=parameter, max_retries=1)
    cfg.http_transport_factory = lambda: httpx.MockTransport(handle)
    provider = LLMProvider(cfg)
    if entrypoint == "complete":
        result = await provider.complete("hello", max_tokens=123)
    else:
        result = await provider.complete_messages([{"role": "user", "content": "hello"}], max_tokens=123)
    assert result.content == "ok"
    assert len(seen) == 1
    assert seen[0][parameter] == 123
    assert ("max_completion_tokens" if parameter == "max_tokens" else "max_tokens") not in seen[0]


@pytest.mark.asyncio
async def test_extra_body_retains_its_existing_sdk_merge_semantics():
    seen = []
    def handle(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "chatcmpl-test", "object": "chat.completion", "created": 1,
            "model": "test", "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "ok"}}]})
    cfg = MemoryConfig()
    cfg.llm = LLMConfig(api_key="test", base_url="https://example.test/v1", output_token_parameter="max_completion_tokens",
                        extra_body={"max_tokens": 77, "max_completion_tokens": 99}, max_retries=1)
    cfg.http_transport_factory = lambda: httpx.MockTransport(handle)
    await LLMProvider(cfg).complete("hello", max_tokens=123)
    assert seen[0]["max_tokens"] == 77 and seen[0]["max_completion_tokens"] == 99
